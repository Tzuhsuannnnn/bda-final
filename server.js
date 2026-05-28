const fs = require("fs");
const dotenv = require("dotenv");

if (fs.existsSync(".env")) {
  dotenv.config();
} else if (fs.existsSync(".env.txt")) {
  dotenv.config({ path: ".env.txt" });
}

const express = require("express");
const Holidays = require("date-holidays");

const app = express();
const preferredPort = Number(process.env.PORT || 3001);

app.use(express.json());
app.use(express.static("."));
app.use((req, res, next) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") {
    return res.sendStatus(204);
  }
  next();
});

const GEMINI_API_KEY = process.env.GEMINI_API_KEY;
const GEMINI_MODEL = String(
  process.env.GEMINI_MODEL || process.env.MODEL_NAME || "gemini-2.5-flash",
).replace(/^models\//, "");
const CWA_API_KEY = process.env.CWA_WEATHER_API_KEY;

function safeRoundNumber(value) {
  const num = Number(value);
  return Number.isFinite(num) ? Math.round(num) : null;
}

function pickFirst(list) {
  return Array.isArray(list) && list.length > 0 ? list[0] : null;
}

function joinNonEmpty(parts, separator = " ") {
  return (parts || []).filter(Boolean).join(separator);
}

function getField(obj, path, defaultValue = null) {
  if (!obj || !path) return defaultValue;
  const keys = String(path).split(".");
  let current = obj;

  for (const key of keys) {
    if (current == null || typeof current !== "object" || !(key in current)) {
      return defaultValue;
    }
    current = current[key];
  }

  return current ?? defaultValue;
}

function buildCwaUrl(datasetId, params = {}) {
  const url = new URL(
    "https://opendata.cwa.gov.tw/api/v1/rest/datastore/" + datasetId,
  );
  url.searchParams.set("Authorization", CWA_API_KEY || "");
  url.searchParams.set("format", "JSON");

  for (const [key, value] of Object.entries(params)) {
    if (value != null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }

  return url.toString();
}

async function fetchJsonWithTimeout(url, timeoutMs = 10000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

function getTaipeiDateString(date = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function parseDateInTaipei(dateString) {
  return new Date(`${dateString}T00:00:00+08:00`);
}

async function fetchCurrentWeather(cityName = "臺北市") {
  if (!CWA_API_KEY) {
    throw new Error("CWA_WEATHER_API_KEY is missing");
  }

  const data = await fetchJsonWithTimeout(
    buildCwaUrl("F-C0032-001", { locationName: cityName }),
  );
  const locations = data?.records?.location || [];
  const location = pickFirst(locations);
  if (!location) {
    throw new Error(`No weather location found for ${cityName}`);
  }

  const weatherElements = getField(location, "weatherElement", []);
  const weatherElement = weatherElements.find(
    (item) => getField(item, "elementName") === "Wx",
  );
  const timeList = getField(weatherElement, "time", []);

  const weatherText = getField(pickFirst(timeList), "parameter.parameterName");
  if (!weatherText) {
    throw new Error(`No weather text found for ${cityName}`);
  }

  return {
    source: "CWA F-C0032-001",
    stationName: getField(location, "locationName", cityName),
    countyName: cityName,
    weatherText,
    updatedAt: new Date().toLocaleString("zh-TW"),
  };
}

async function fetchEarthquakeSummary() {
  if (!CWA_API_KEY) return null;
  try {
    const data = await fetchJsonWithTimeout(buildCwaUrl("E-A0015-001"));
    const earthquakes = data?.records?.Earthquake || [];
    const latest = pickFirst(earthquakes);
    if (!latest) return null;

    const earthquakeInfo = getField(latest, "EarthquakeInfo", {});
    const magnitude =
      getField(earthquakeInfo, "EarthquakeMagnitude.MagnitudeValue") ||
      getField(earthquakeInfo, "EarthquakeMagnitude.MagnitudeType");
    const depth =
      getField(earthquakeInfo, "FocalDepth") ||
      getField(earthquakeInfo, "Depth");
    const epicenter =
      getField(earthquakeInfo, "Epicenter.EpicenterName") ||
      getField(earthquakeInfo, "Epicenter.Location") ||
      "未知震央";
    const originTime =
      getField(earthquakeInfo, "OriginTime") ||
      getField(latest, "ReportInfo.OriginTime");

    return {
      source: "CWA E-A0015-001",
      originTime,
      magnitude,
      depth,
      epicenter,
    };
  } catch (e) {
    console.warn("fetchEarthquakeSummary error:", e.message);
    return null;
  }
}

// 🛠️ 升級：併發抓取 豪大雨(003)、低溫(004)、高溫(005) 資訊並進行時間篩選
async function fetchClimateAlerts(targetCounty = "臺北市") {
  if (!CWA_API_KEY) return [];

  const datasets = [
    { id: "W-C0033-003", type: "豪大雨資訊" },
    { id: "W-C0033-004", type: "低溫資訊" },
    { id: "W-C0033-005", type: "高溫資訊" },
  ];

  // 計算今天 +- 5天的時間戳記 (毫秒)
  const todayStr = getTaipeiDateString();
  const todayMid = parseDateInTaipei(todayStr).getTime();
  const fiveDaysMs = 5 * 24 * 60 * 60 * 1000;
  const minTime = todayMid - fiveDaysMs;
  const maxTime = todayMid + fiveDaysMs + (24 * 60 * 60 * 1000 - 1); // 算到第 +5 天的深夜

  function isWithinWindow(startTimeStr, endTimeStr) {
    const startMs = new Date(startTimeStr).getTime();
    const endMs = new Date(endTimeStr).getTime();

    if (Number.isNaN(startMs) || Number.isNaN(endMs)) return false;
    return startMs <= maxTime && endMs >= minTime;
  }

  const alertPromises = datasets.map(async (dataset) => {
    try {
      const url = buildCwaUrl(dataset.id);
      const data = await fetchJsonWithTimeout(url);
      const infos = data?.records?.info || [];
      const extracted = [];

      for (const info of infos) {
        const areas = (info?.area || [])
          .map((area) => getField(area, "areaDesc", ""))
          .filter(Boolean);

        const startTimeStr =
          info.onset || info.effective || info.expires || null;
        const endTimeStr = info.expires || info.onset || info.effective || null;

        if (!startTimeStr || !endTimeStr) continue;
        if (!isWithinWindow(startTimeStr, endTimeStr)) continue;

        const severityLevel =
          (info.parameter || []).find(
            (item) => item.valueName === "severity_level",
          )?.value ||
          info.severity ||
          "";
        const alertTitle =
          (info.parameter || []).find(
            (item) => item.valueName === "alert_title",
          )?.value ||
          info.headline ||
          dataset.type;

        extracted.push({
          datasetId: dataset.id,
          alertType: dataset.type,
          headline: info.headline || alertTitle,
          county: areas.join("、") || targetCounty,
          description: info.description || alertTitle,
          severityLevel,
          startTime: startTimeStr,
          endTime: endTimeStr,
          web: info.web || null,
          responseType: info.responseType || null,
        });
      }
      return extracted;
    } catch (err) {
      throw new Error(`Fetch dataset ${dataset.id} failed: ${err.message}`);
    }
  });

  const results = await Promise.all(alertPromises);
  const flat = results.flat();

  // 按 datasetId 分群，並選出與今天時間中點最接近的一筆（只回傳每個 dataset 一筆）
  const groups = {};
  for (const it of flat) {
    const startMs = it.startTime ? new Date(it.startTime).getTime() : null;
    const endMs = it.endTime ? new Date(it.endTime).getTime() : null;
    const mid =
      startMs && endMs ? (startMs + endMs) / 2 : startMs || endMs || 0;
    if (!groups[it.datasetId]) groups[it.datasetId] = [];
    groups[it.datasetId].push({ item: it, mid });
  }

  const selected = [];
  for (const k of Object.keys(groups)) {
    const list = groups[k];
    list.sort(
      (a, b) => Math.abs(a.mid - todayMid) - Math.abs(b.mid - todayMid),
    );
    if (list.length > 0) selected.push(list[0].item);
  }

  return selected;
}

// 颱風特報：C0034-005，抓 +-7 天的範圍，支援不同回傳格式（info 或 TropicalCyclones）
async function fetchTyphoonAlerts(targetCounty = "臺北市") {
  if (!CWA_API_KEY) return [];

  const datasetId = "W-C0034-005";
  const todayStr = getTaipeiDateString();
  const todayMid = parseDateInTaipei(todayStr).getTime();
  const sevenDaysMs = 7 * 24 * 60 * 60 * 1000;
  const minTime = todayMid - sevenDaysMs;
  const maxTime = todayMid + sevenDaysMs + (24 * 60 * 60 * 1000 - 1);

  function isWithinSevenDays(startTimeStr, endTimeStr) {
    const startMs = new Date(startTimeStr).getTime();
    const endMs = new Date(endTimeStr).getTime();
    if (Number.isNaN(startMs) || Number.isNaN(endMs)) return false;
    return startMs <= maxTime && endMs >= minTime;
  }

  try {
    // 嘗試多個常見的 resource id，提升容錯
    const candidateIds = ["W-C0034-005", "W-C0034-001", "C0034-005"];
    let data = null;
    let usedId = null;
    let lastErr = null;
    for (const candidate of candidateIds) {
      try {
        const url = buildCwaUrl(candidate);
        data = await fetchJsonWithTimeout(url);
        usedId = candidate;
        break;
      } catch (e) {
        lastErr = e;
        continue;
      }
    }
    if (!data) {
      throw new Error(
        `All typhoon dataset fetch attempts failed: ${lastErr?.message || "no response"}`,
      );
    }
    const infos = data?.records?.info || [];
    const extracted = [];

    // 如果 API 回傳的是標準 info 陣列（舊式格式）
    if (Array.isArray(infos) && infos.length > 0) {
      for (const info of infos) {
        const areas = (info?.area || [])
          .map((area) => getField(area, "areaDesc", ""))
          .filter(Boolean);

        const startTimeStr =
          info.onset || info.effective || info.expires || null;
        const endTimeStr = info.expires || info.onset || info.effective || null;

        if (!startTimeStr || !endTimeStr) continue;
        if (!isWithinSevenDays(startTimeStr, endTimeStr)) continue;

        const alertTitle =
          (info.parameter || []).find(
            (item) => item.valueName === "alert_title",
          )?.value ||
          info.headline ||
          "颱風警報";

        extracted.push({
          datasetId,
          alertType: "颱風警報",
          headline: info.headline || alertTitle,
          county: areas.join("、") || targetCounty,
          description: info.description || alertTitle,
          startTime: startTimeStr,
          endTime: endTimeStr,
          web: info.web || null,
        });
      }
    }

    // 如果 API 回傳的是 TropicalCyclones 結構 (C0034-005 常見)
    const tcs = data?.records?.TropicalCyclones?.TropicalCyclone || [];
    if (Array.isArray(tcs) && tcs.length > 0) {
      for (const tc of tcs) {
        const name =
          tc.CwaTyphoonName || tc.TyphoonName || tc.CwaTdNo || "颱風";
        const fixes = tc.AnalysisData?.Fix || [];
        if (!Array.isArray(fixes) || fixes.length === 0) continue;
        const first = fixes[0];
        const last = fixes[fixes.length - 1];

        const startTimeStr = first.DateTime || first.Date || null;
        const endTimeStr = last.DateTime || last.Date || null;

        if (!startTimeStr || !endTimeStr) continue;
        if (!isWithinSevenDays(startTimeStr, endTimeStr)) continue;

        const fromDir =
          first.MovingDirection || first.Movement || first.Moving || "未知";
        const toDir =
          last.MovingDirection || last.Movement || last.Moving || "未知";

        const description = `${name} 從 ${fromDir} 向 ${toDir} 移動`;
        console.log(
          `fetchTyphoonAlerts: found ${name}, fixes=${fixes.length}, start=${startTimeStr}, end=${endTimeStr}, usedId=${usedId}`,
        );

        extracted.push({
          datasetId,
          alertType: "颱風警報",
          headline: name,
          county: targetCounty,
          description,
          startTime: startTimeStr,
          endTime: endTimeStr,
          web: null,
        });
      }
    }

    if (extracted.length === 0) return [];

    // 選出接近今天中點的一筆
    extracted.sort((a, b) => {
      const aMid =
        (new Date(a.startTime).getTime() + new Date(a.endTime).getTime()) / 2;
      const bMid =
        (new Date(b.startTime).getTime() + new Date(b.endTime).getTime()) / 2;
      return Math.abs(aMid - todayMid) - Math.abs(bMid - todayMid);
    });

    return [extracted[0]];
  } catch (err) {
    throw new Error(`Fetch dataset ${datasetId} failed: ${err.message}`);
  }
}

async function fetchMajorDisasterSummary(targetCounty = "臺北市") {
  let earthquake = null;
  let climateAlerts = [];
  let typhoonAlerts = [];

  try {
    earthquake = await fetchEarthquakeSummary();
  } catch (err) {
    console.warn("fetchEarthquakeSummary failed:", err.message);
  }

  try {
    climateAlerts = await fetchClimateAlerts(targetCounty);
  } catch (err) {
    console.warn("fetchClimateAlerts failed:", err.message);
    climateAlerts = [];
  }

  try {
    typhoonAlerts = await fetchTyphoonAlerts(targetCounty);
  } catch (err) {
    console.warn("fetchTyphoonAlerts failed:", err.message);
    typhoonAlerts = [];
  }

  const alerts = (climateAlerts || []).concat(typhoonAlerts || []);

  const parts = [];

  if (earthquake) {
    parts.push(
      `地震：${joinNonEmpty([
        earthquake.originTime ? `發生時間 ${earthquake.originTime}` : null,
        earthquake.magnitude ? `規模 ${earthquake.magnitude}` : null,
        earthquake.depth != null ? `深度 ${earthquake.depth} 公里` : null,
        earthquake.epicenter ? `震央 ${earthquake.epicenter}` : null,
      ])}`,
    );
  }

  if (alerts && alerts.length > 0) {
    const alertSummaries = alerts
      .slice(0, 3)
      .map((item) => `[${item.alertType}] ${item.county} ${item.description}`);
    parts.push(`氣候特警報(+-5天): ${alertSummaries.join("；")}`);
  }

  if (parts.length === 0) {
    parts.push("目前無明顯地震、豪大雨、大高低溫警報");
  }

  return {
    source: "CWA E-A0015-001 + W-C0033-003/004/005 + W-C0034-005",
    earthquake,
    alerts,
    summaryText: parts.join("｜"),
  };
}

// 🛠️ 修正：允許前端傳遞自訂生成的動態特報項目金鑰進行解包
function buildSelectedContextLinesFromKeys({
  selectedContextKeys = [],
  weatherInfo,
  disasterInfo,
  holidayInfo,
}) {
  const lines = [];

  for (const key of selectedContextKeys) {
    if (key === "weatherText" && weatherInfo?.weatherText) {
      lines.push(`現在天氣: ${weatherInfo.weatherText}`);
      continue;
    }

    if (key === "earthquake" && disasterInfo?.earthquake) {
      const earthquakeText = formatEarthquakeContext(disasterInfo.earthquake);
      if (earthquakeText) lines.push(`地震資訊: ${earthquakeText}`);
      continue;
    }

    // 🔍 支援動態特報 Checkbox 轉換為 Prompt 內文
    if (key.startsWith("climateAlert:")) {
      const alertIdx = parseInt(key.slice("climateAlert:".length), 10);
      const alertItem = disasterInfo?.alerts?.[alertIdx];
      if (alertItem) {
        lines.push(
          `氣候警戒[${alertItem.alertType}]: ${alertItem.county}${alertItem.description} (期間: ${alertItem.startTime} 至 ${alertItem.endTime})`,
        );
      }
      continue;
    }

    if (key.startsWith("holiday:")) {
      const dateString = key.slice("holiday:".length);
      const item = (holidayInfo?.upcoming || []).find(
        (entry) => entry.dateString === dateString,
      );
      if (item) {
        lines.push(`節慶/活動: ${buildHolidayChoiceText(item)}`);
      }
      continue;
    }
  }

  return lines;
}

function buildSelectedContextLines({
  selectedContextKeys = [],
  weatherInfo,
  disasterInfo,
  holidayInfo,
}) {
  return buildSelectedContextLinesFromKeys({
    selectedContextKeys,
    weatherInfo,
    disasterInfo,
    holidayInfo,
  });
}

async function fetchUpcomingHolidaySummary(
  countryCode = "TW",
  withinDays = 30,
) {
  const todayString = getTaipeiDateString();
  const today = parseDateInTaipei(todayString);
  const currentYear = Number(todayString.slice(0, 4));
  const years = [currentYear, currentYear + 1];
  const hd = new Holidays(countryCode);
  const holidayRows = years
    .flatMap((year) => hd.getHolidays(year) || [])
    .filter((holiday) => holiday.type === "public");

  const upcoming = holidayRows
    .map((holiday) => {
      const holidayDate = new Date(holiday.date);
      const diffDays = Math.round(
        (holidayDate.getTime() - today.getTime()) / 86400000,
      );
      return {
        dateString: getTaipeiDateString(holidayDate),
        diffDays,
        name: holiday.name,
        nameEn: holiday.name,
        types: [holiday.type].filter(Boolean),
      };
    })
    .filter(
      (holiday) => holiday.diffDays >= 0 && holiday.diffDays <= withinDays,
    )
    .sort((a, b) => a.diffDays - b.diffDays);

  const nearest = pickFirst(upcoming);
  if (!nearest) {
    return {
      source: "date-holidays TW",
      today: todayString,
      summaryText: `${withinDays} 天內無節慶`,
      upcoming: [],
    };
  }

  return {
    source: "date-holidays TW",
    today: todayString,
    summaryText: `${nearest.name} (${nearest.dateString}，${nearest.diffDays} 天後)`,
    upcoming,
  };
}

function formatWeatherContext(weatherInfo) {
  const summaryParts = [];
  if (weatherInfo?.stationName)
    summaryParts.push(`測站 ${weatherInfo.stationName}`);
  if (weatherInfo?.countyName)
    summaryParts.push(`地區 ${weatherInfo.countyName}`);
  if (weatherInfo?.weatherText)
    summaryParts.push(`天氣 ${weatherInfo.weatherText}`);
  if (weatherInfo?.updatedAt)
    summaryParts.push(`更新 ${weatherInfo.updatedAt}`);
  return summaryParts.join("｜") || "天氣資訊未知";
}

function formatDisasterContext(disasterInfo) {
  return disasterInfo?.summaryText || "目前無明顯地震與劇烈氣候警報";
}

function formatHolidayContext(holidayInfo) {
  if (!holidayInfo?.upcoming?.length) return "30 天內無節慶";
  return holidayInfo.upcoming
    .map(
      (holiday) =>
        `${holiday.name}（${holiday.dateString}，${holiday.diffDays} 天後）`,
    )
    .join("｜");
}

function buildHolidayChoiceText(item) {
  if (!item) return "";
  return [
    item.name || item.nameEn || "節慶",
    item.dateString ? `（${item.dateString}` : "（",
    typeof item.diffDays === "number" ? `，${item.diffDays} 天後）` : "）",
  ]
    .join("")
    .replace("（，", "（");
}

function formatEarthquakeContext(earthquake) {
  if (!earthquake) return null;
  return joinNonEmpty([
    earthquake.originTime ? `發生時間 ${earthquake.originTime}` : null,
    earthquake.magnitude ? `規模 ${earthquake.magnitude}` : null,
    earthquake.depth != null ? `深度 ${earthquake.depth} 公里` : null,
    earthquake.epicenter ? `震央 ${earthquake.epicenter}` : null,
  ]);
}

app.post("/api/context", async (req, res) => {
  try {
    const { userData } = req.body || {};
    const cityName = userData?.cityName || "臺北市";

    const weatherInfo = await fetchCurrentWeather(cityName);
    const [disasterInfo, holidayInfo] = await Promise.all([
      fetchMajorDisasterSummary(cityName),
      fetchUpcomingHolidaySummary("TW", 30),
    ]);

    return res.json({
      context: {
        weather: weatherInfo,
        disaster: disasterInfo,
        holiday: holidayInfo,
      },
      summary: {
        weatherText: formatWeatherContext(weatherInfo),
        disasterText: formatDisasterContext(disasterInfo),
        holidayText: formatHolidayContext(holidayInfo),
      },
    });
  } catch (err) {
    console.error(err);
    return res
      .status(500)
      .json({ error: "server error", details: err.message });
  }
});

function buildUserPersonaPrompt(userData) {
  const intentLabel = userData?.intentLabel || "";

  if (intentLabel === "款式猶豫") {
    return [
      "會員 A - 款式猶豫 (高頻切換同類商品)",
      "核心心理卡關點：資訊過載、害怕選錯、比較成本高。",
      "文案主要任務：做減法，幫忙分類與推薦。",
      "行銷切入點：專家指南、情境標籤、懶人包。",
    ].join("\n");
  }

  if (intentLabel === "規格猶豫") {
    return [
      "會員 B - 規格猶豫 (單頁停留過久)",
      "核心心理卡關點：不確定性、缺乏安全感、風險感知。",
      "文案主要任務：做加法，提供證據與細節。",
      "行銷切入點：厚實證言、痛點對照、條款白話化。",
    ].join("\n");
  }

  if (intentLabel === "價格猶豫") {
    return [
      "會員 C - 價格猶豫 (購物車滯留)",
      "核心心理卡關點：覺得不划算、卡在臨門一腳。",
      "文案主要任務：臨門一腳，放大價值感、降低痛感。",
      "行銷切入點：換算降維、利益疊加、損失厭惡。",
    ].join("\n");
  }

  return "會員狀態：一般推薦";
}

async function generateGeminiCopy(prompt) {
  if (!GEMINI_API_KEY) {
    throw new Error("GEMINI_API_KEY is missing");
  }

  const url = `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent?key=${encodeURIComponent(GEMINI_API_KEY)}`;
  const body = {
    contents: [
      {
        role: "user",
        parts: [{ text: prompt }],
      },
    ],
    generationConfig: {
      temperature: 0.25,
      //maxOutputTokens: 200,
    },
  };

  const fetchRes = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!fetchRes.ok) {
    const errText = await fetchRes.text();
    throw new Error(`Model API error: ${errText}`);
  }

  const json = await fetchRes.json();
  const text = (json?.candidates || [])
    .flatMap((candidate) => candidate?.content?.parts || [])
    .map((part) => part?.text || "")
    .join("")
    .trim();

  if (!text) {
    throw new Error("Model returned no text");
  }

  return text;
}

app.post("/api/generate", async (req, res) => {
  try {
    const {
      userKey,
      weather: clientWeather,
      festival: clientFestival,
      userData,
      selectedContextKeys = [],
      contextData,
    } = req.body || {};

    if (!userKey) return res.status(400).json({ error: "missing userKey" });

    const cityName = userData?.cityName || "臺北市";
    const weatherInfo =
      contextData?.weather || (await fetchCurrentWeather(cityName));
    const disasterInfo =
      contextData?.disaster || (await fetchMajorDisasterSummary(cityName));
    const holidayInfo =
      contextData?.holiday || (await fetchUpcomingHolidaySummary("TW", 30));

    const weatherText =
      weatherInfo?.weatherText || clientWeather || "天氣資訊未知";
    const weatherSummary = joinNonEmpty(
      [
        weatherInfo?.stationName ? `測站 ${weatherInfo.stationName}` : null,
        weatherInfo?.countyName ? `地區 ${weatherInfo.countyName}` : null,
        weatherText ? `天氣 ${weatherText}` : null,
      ],
      "｜",
    );

    const selectedContextLines = buildSelectedContextLines({
      selectedContextKeys,
      weatherInfo,
      disasterInfo,
      holidayInfo,
    });

    const selectedContextText = selectedContextLines.length
      ? selectedContextLines.join("\n")
      : `現在天氣: ${weatherSummary || clientWeather || "天氣資訊未知"}\n節慶/活動（30 天內）: ${holidayInfo?.summaryText || clientFestival || "30 天內無節慶"}\n重大自然災害/警報: ${disasterInfo?.summaryText || "目前無明顯地震或氣候警報"}`;

    const productInfo = userData
      ? `主商品: ${userData.mainProduct} (NT$${userData.mainPrice}), 互補品: ${userData.recProduct} (NT$${userData.recPrice})`
      : "";
    const userLabel = userData?.intentLabel
      ? `使用者意圖: ${userData.intentLabel}`
      : "";
    const user = buildUserPersonaPrompt(userData);

    const prompt = `你是一位電商line訊息文案撰寫員，要簡短文案。根據外部資訊(不用在乎縣市)，輸出最終文案，不要解釋、不要標題、不要角色說明。\n\n需求：針對使用者的猶豫點，語氣真誠、生活化，提到主商品與互補品。\n\n商品資訊：${productInfo}\n${userLabel}\n${user}\n外部資訊:\n${selectedContextText}`;
    let text = await generateGeminiCopy(prompt);

    return res.json({
      text,
      promptUsed: prompt,
      context: {
        weather: weatherInfo,
        holiday: holidayInfo,
        disaster: disasterInfo,
      },
      model: "gemini",
      modelName: GEMINI_MODEL,
    });
  } catch (err) {
    console.error(err);
    return res
      .status(500)
      .json({ error: "server error", details: err.message });
  }
});

function startServer(portIndex = 0) {
  const port = preferredPort;
  const server = app.listen(port, () => {
    console.log(`Server listening on http://localhost:${port}`);
  });

  server.on("error", (error) => {
    console.error(error);
    process.exit(1);
  });
}

startServer();
