"""Deterministic local hash vectors for hybrid retrieval."""
from __future__ import annotations

import hashlib
import json
import math
import re

from .core import json_text


MODEL_ID = "knowledgeos-hash-embedding-v1"
DIMENSIONS = 192


def vector(text):
    values = [0.0] * DIMENSIONS
    normalized = str(text).lower()
    words = re.findall(r"[\w:-]+", normalized, re.UNICODE)
    han = []
    for sequence in re.findall(r"[\u4e00-\u9fff]+", normalized):
        han.extend(sequence)
        han.extend(sequence[i:i + 2] for i in range(len(sequence) - 1))
    for token in words + han:
        hashed = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(hashed[:4], "big") % DIMENSIONS
        values[index] += 1.0 if hashed[4] % 2 == 0 else -1.0
    norm = math.sqrt(sum(item * item for item in values))
    return [item / norm for item in values] if norm else values


class SemanticIndex:
    def __init__(self, db):
        self.db = db

    def index(self, node_id, text, source_hash):
        self.db.write("""INSERT INTO node_embedding(node_id,model_id,dimensions,vector_json,source_hash) VALUES(?,?,?,?,?)
          ON CONFLICT(node_id) DO UPDATE SET model_id=excluded.model_id,dimensions=excluded.dimensions,
          vector_json=excluded.vector_json,source_hash=excluded.source_hash""",
          (node_id, MODEL_ID, DIMENSIONS, json_text(vector(text)), source_hash))

    def search(self, query, typ=None, limit=50):
        query_vector = vector(query)
        sql = "SELECT e.vector_json,n.id,n.type,n.label,n.lifecycle,n.source_class FROM node_embedding e JOIN node n ON n.id=e.node_id"
        binds = []
        if typ:
            sql += " WHERE n.type=?"
            binds.append(typ)
        rows = []
        for row in self.db.execute(sql, binds):
            score = sum(a * b for a, b in zip(query_vector, json.loads(row["vector_json"])))
            if score > 0.05:
                rows.append({**{k: v for k, v in row.items() if k != "vector_json"}, "vector_score": score})
        return sorted(rows, key=lambda row: -row["vector_score"])[:limit]
