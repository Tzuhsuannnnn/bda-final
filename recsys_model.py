import torch
import torch.nn as nn


class TwoTowerModel(nn.Module):
    def __init__(
        self,
        user_numeric_dim,
        item_numeric_dim,
        text_dim,
        gender_vocab_size,
        member_level_vocab_size,
        user_embed_dim=64,
        item_embed_dim=64,
        gender_embed_dim=4,
        member_level_embed_dim=8,
        hidden_dim=128,
        dropout=0.1,
    ):
        super().__init__()
        self.gender_embedding = nn.Embedding(gender_vocab_size, gender_embed_dim)
        self.member_level_embedding = nn.Embedding(
            member_level_vocab_size, member_level_embed_dim
        )

        user_input_dim = user_numeric_dim + gender_embed_dim + member_level_embed_dim
        self.user_mlp = nn.Sequential(
            nn.Linear(user_input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, user_embed_dim),
        )

        item_input_dim = item_numeric_dim + text_dim
        self.item_mlp = nn.Sequential(
            nn.Linear(item_input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, item_embed_dim),
        )

    def encode_user(self, user_numeric, gender_idx, member_level_idx):
        gender_emb = self.gender_embedding(gender_idx)
        level_emb = self.member_level_embedding(member_level_idx)
        features = torch.cat([user_numeric, gender_emb, level_emb], dim=1)
        return self.user_mlp(features)

    def encode_item(self, item_numeric, item_text):
        features = torch.cat([item_numeric, item_text], dim=1)
        return self.item_mlp(features)

    def forward(
        self,
        user_numeric,
        gender_idx,
        member_level_idx,
        item_numeric,
        item_text,
    ):
        user_vec = self.encode_user(user_numeric, gender_idx, member_level_idx)
        item_vec = self.encode_item(item_numeric, item_text)
        return (user_vec * item_vec).sum(dim=1)
