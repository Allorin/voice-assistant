"""Гибридный поиск по записям.

Оценка складывается из:
1. BM25 по леммам (точные совпадения слов в любой форме);
2. синонимов («созвон» найдёт «встречу», «контракт» — «договор») с пониженным весом;
3. нечёткого совпадения (опечатки, редкие формы слов) — rapidfuzz;
4. опционально — косинусной близости эмбеддингов (настоящий семантический поиск), если задан провайдер.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.models import Item
from app.search.text import lemmas, synonyms

K1, B = 1.2, 0.75
W_EXACT, W_FUZZY, W_PREFIX, W_SYNONYM = 1.0, 0.8, 0.7, 0.5


@dataclass
class Hit:
    item: Item
    score: float
    matched: list[str] = field(default_factory=list)


def _term_match(term: str, doc: list[str]) -> tuple[float, int]:
    """Лучший вес совпадения термина с документом и число вхождений."""
    exact = doc.count(term)
    if exact:
        return W_EXACT, exact
    best, count = 0.0, 0
    for d in doc:
        w = 0.0
        if len(term) >= 4 and len(d) >= 4 and fuzz.ratio(term, d) >= 80:
            w = W_FUZZY
        elif len(term) >= 5 and len(d) >= 5 and term[:5] == d[:5]:
            w = W_PREFIX
        if w > best:
            best, count = w, 1
        elif w == best and w > 0:
            count += 1
    return best, count


class SearchEngine:
    def __init__(self, embedder=None):
        self.embedder = embedder

    def rank(self, query: str, items: list[Item], min_relative: float = 0.3) -> list[Hit]:
        q_terms = list(dict.fromkeys(lemmas(query)))
        if not q_terms or not items:
            return []
        docs = [lemmas(it.title) for it in items]
        n_docs = len(docs)
        avgdl = sum(len(d) for d in docs) / n_docs or 1.0
        df = Counter()
        for d in docs:
            for t in set(d):
                df[t] += 1

        # запрос + синонимы (с меньшим весом)
        weighted: list[tuple[str, float, str]] = [(t, 1.0, t) for t in q_terms]
        for t in q_terms:
            for s in synonyms(t):
                if s not in q_terms:
                    weighted.append((s, W_SYNONYM, t))

        def idf(term: str) -> float:
            n = df.get(term, 0)
            return math.log(1 + (n_docs - n + 0.5) / (n + 0.5))

        hits: list[Hit] = []
        for item, doc in zip(items, docs):
            score, matched_origins, matched_words = 0.0, set(), []
            for term, qw, origin in weighted:
                w, tf = _term_match(term, doc)
                if not w:
                    continue
                norm_tf = tf * (K1 + 1) / (tf + K1 * (1 - B + B * len(doc) / avgdl))
                score += qw * w * max(idf(term), 0.1) * norm_tf
                matched_origins.add(origin)
                matched_words.append(term)
            if score <= 0:
                continue
            coverage = len(matched_origins) / len(q_terms)
            hits.append(Hit(item, score * (0.4 + 0.6 * coverage), matched_words))

        if self.embedder is not None:
            hits = self._blend_embeddings(query, items, hits)

        if not hits:
            return []
        hits.sort(key=lambda h: h.score, reverse=True)
        top = hits[0].score
        return [h for h in hits if h.score >= top * min_relative]

    def _blend_embeddings(self, query: str, items: list[Item], hits: list[Hit]) -> list[Hit]:
        try:
            sims = self.embedder.similarities(query, [it.title for it in items])
        except Exception:  # эмбеддинги — улучшение, а не обязательная часть
            return hits
        lexical = {h.item.id: h for h in hits}
        max_lex = max((h.score for h in hits), default=1.0) or 1.0
        blended: list[Hit] = []
        for item, sim in zip(items, sims):
            lex = lexical.get(item.id)
            lex_norm = lex.score / max_lex if lex else 0.0
            score = 0.65 * lex_norm + 0.35 * max(sim, 0.0)
            if lex or sim >= self.embedder.threshold:
                blended.append(Hit(item, score, lex.matched if lex else ["~смысл"]))
        return blended
