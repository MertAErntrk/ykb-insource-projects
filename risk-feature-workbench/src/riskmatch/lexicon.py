"""Türkçe/İngilizce kolon adı ve açıklamalarını ortak kanonik tokenlara indirir."""
import json
import os
import re
import unicodedata

from .errors import ConfigError

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "lexicon.json")
_DEFAULT = None

_CAMEL_LOWER_UPPER = re.compile(r"(?<=[a-zçğıöşü])(?=[A-ZÇĞİÖŞÜ])")
_CAMEL_ACRONYM = re.compile(r"(?<=[A-ZÇĞİÖŞÜ])(?=[A-ZÇĞİÖŞÜ][a-zçğıöşü])")
_APOSTROPHE = re.compile(r"['’`´][a-zA-ZçğıöşüÇĞİÖŞÜ]+")

# L = son (L3M, L2W); W = pencere (katalog adlarında W1W, W1M, W3M).
_WINDOW_PREFIX = {"son": True, "last": True, "past": True, "previous": True, "l": False, "w": False}
# Sayısız Excel kısaltmaları: LM son 1 ay, LW son 1 hafta.
_LAST_SHORT = {"lm": "ay", "lw": "hafta"}
_UNITS = {
    "gun": "gun", "gunluk": "gun", "day": "gun", "days": "gun", "dy": "gun",
    "hafta": "hafta", "haftalik": "hafta", "week": "hafta", "weeks": "hafta", "wk": "hafta", "wks": "hafta",
    "ay": "ay", "aylik": "ay", "aylar": "ay", "month": "ay", "months": "ay", "mo": "ay", "mth": "ay",
    "yil": "yil", "yillik": "yil", "yillar": "yil", "year": "yil", "years": "yil", "yr": "yil", "yrs": "yil",
}
_SHORT_UNITS = {"d": "gun", "w": "hafta", "m": "ay", "y": "yil"}
_NUMBER_WORDS = {
    "bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5, "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "on": 10, "ten": 10, "onbir": 11, "oniki": 12, "eleven": 11, "twelve": 12,
    "yirmi": 20, "otuz": 30, "kirk": 40, "elli": 50, "altmis": 60, "yetmis": 70, "seksen": 80, "doksan": 90,
}
_UNKNOWN_SKIP_SUFFIXES = frozenset(["in", "un", "li", "da", "de", "ta", "te", "ya", "ye", "na", "ne", "es"])
_SOFTEN = {"b": "p", "d": "t", "g": "k"}
_WINDOW_TEXT = {"gun": "gün", "hafta": "hafta", "ay": "ay", "yil": "yıl"}


def fold(text):
    value = str(text or "").replace("İ", "I").replace("ı", "i")
    value = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _words(text):
    """Kimlik/cümle metnini katlanmış alfa ya da rakam parçalarına böler."""
    value = _APOSTROPHE.sub("", str(text or ""))
    value = _CAMEL_ACRONYM.sub(" ", _CAMEL_LOWER_UPPER.sub(" ", value))
    parts = []
    for chunk in re.findall(r"[a-z0-9]+", fold(value)):
        parts.extend(re.findall(r"[a-z]+|[0-9]+", chunk))
    return parts


def _phrase(value):
    return " ".join(re.findall(r"[a-z0-9]+", fold(value)))


def _soften(root):
    return root[:-1] + _SOFTEN[root[-1]] if root and root[-1] in _SOFTEN else root


def _normalize_data(data, source="sözlük"):
    if not isinstance(data, dict) or data.get("version", 1) != 1:
        raise ConfigError("Sözlük dosyası geçersiz: şema sürümü desteklenmiyor.")
    groups = data.get("groups", [])
    if not isinstance(groups, list):
        raise ConfigError("Sözlük dosyası geçersiz: 'groups' liste olmalı.")
    clean = []
    for group in groups:
        if not isinstance(group, list) or not group or not all(isinstance(m, str) for m in group):
            raise ConfigError("Sözlük dosyası geçersiz: her grup metin listesi olmalı.")
        members = []
        for member in group:
            member = _phrase(member)
            if member and member not in members:
                members.append(member)
        if members:
            clean.append(members)
    expansions = {}
    raw = data.get("expansions", {})
    if not isinstance(raw, dict):
        raise ConfigError("Sözlük dosyası geçersiz: 'expansions' nesne olmalı.")
    for key, values in raw.items():
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ConfigError("Sözlük dosyası geçersiz: açılımlar metin listesi olmalı.")
        expansions[_phrase(key)] = [_phrase(v) for v in values if _phrase(v)]
    result = {"groups": clean, "expansions": expansions}
    for field in ("generic", "suffixes", "structural"):
        values = data.get(field, [])
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ConfigError("Sözlük dosyası geçersiz: '%s' metin listesi olmalı." % field)
        result[field] = [_phrase(v) for v in values if _phrase(v)]
    return result


def _merge(base, extra):
    """extra'yı base'in üzerine ekler; var olan üye başka gruba taşınmaz."""
    groups = [list(g) for g in base["groups"]]
    index = {}
    for position, group in enumerate(groups):
        for member in group:
            index.setdefault(member, position)
    for group in extra["groups"]:
        target = index.get(group[0])
        if target is None:
            groups.append([])
            target = len(groups) - 1
        for member in group:
            if member not in index:
                groups[target].append(member)
                index[member] = target
            elif index[member] == target and member not in groups[target]:
                groups[target].append(member)
    expansions = dict(base["expansions"])
    expansions.update(extra["expansions"])
    merged = {"groups": [g for g in groups if g], "expansions": expansions}
    for field in ("generic", "suffixes", "structural"):
        merged[field] = list(base[field]) + [v for v in extra[field] if v not in base[field]]
    return merged


class Lexicon:
    def __init__(self, data):
        self._data = _normalize_data(data)
        self._canon = {}
        self._group_canons = set()
        self._phrase_raw = {}
        self._phrase_stem = {}
        self._suffixes = sorted(set(self._data["suffixes"]), key=lambda s: (-len(s), s))
        self._cache = {}
        self._stem_cache = {}
        self._generic = set(self._data["generic"])
        pending = []
        for group in self._data["groups"]:
            canon = group[0].replace(" ", "_")
            if " " not in group[0]:
                self._group_canons.add(canon)
            for member in group:
                if " " in member:
                    pending.append((member.split(), canon))
                else:
                    self._canon.setdefault(member, canon)
        for word in self._generic:
            self._canon.setdefault(word, word)
        for key in self._data["expansions"]:
            self._canon.setdefault(key, key)
        # Yapısal tokenlar (ölçü, yön, birleştirme) kanonik biçimleriyle tutulur; win:* önek kuralıdır.
        self._structural = set()
        for word in self._data["structural"]:
            self._structural.add(word.replace(" ", "_"))
            self._structural.add(self._canon.get(word, word))
        self._max_phrase = 1
        for words, canon in pending:
            self._phrase_raw.setdefault(tuple(words), canon)
            self._phrase_stem.setdefault(tuple(self._pstem(w) for w in words), canon)
            self._max_phrase = max(self._max_phrase, len(words))
        self._expansions = {}
        for key, values in self._data["expansions"].items():
            out = []
            for value in values:
                out.extend(self._norm_word(w) for w in value.split())
            self._expansions[self._canon[key]] = out

    # --- tek kelime normalizasyonu ---
    def _pstem(self, word):
        cached = self._stem_cache.get(word)
        if cached is None:
            cached = word
            if not word.isdigit():
                for suffix in self._suffixes:
                    if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                        cached = _soften(word[:-len(suffix)])
                        break
            self._stem_cache[word] = cached
        return cached

    def _norm_word(self, word):
        cached = self._cache.get(word)
        if cached is not None:
            return cached
        result = word
        if word in self._canon:
            result = self._canon[word]
        elif not word.isdigit():
            found = False
            for suffix in self._suffixes:
                if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                    root = word[:-len(suffix)]
                    for candidate in (root, _soften(root)):
                        # 3 harfli kısaltmalar yalnız tam token olarak kanona gider ("bale", "mine", "tele");
                        # kısa kök ancak kendi kanonuysa (gün, yaş, kod) ek soyulur.
                        if candidate in self._canon and (len(candidate) >= 4 or self._canon[candidate] == candidate):
                            result, found = self._canon[candidate], True
                            break
                    if found:
                        break
            if not found:
                for suffix in self._suffixes:
                    if (len(suffix) >= 2 and suffix not in _UNKNOWN_SKIP_SUFFIXES
                            and word.endswith(suffix) and len(word) - len(suffix) >= 4):
                        result = word[:-len(suffix)]
                        break
        self._cache[word] = result
        return result

    def known(self, word):
        return self._norm_word(word) in self._group_canons or word in self._canon

    # --- pencere ---
    def _unit(self, word, single_ok):
        if word in _UNITS:
            return _UNITS[word]
        if single_ok and word in _SHORT_UNITS:
            return _SHORT_UNITS[word]
        for suffix in self._suffixes:
            if word.endswith(suffix) and len(word) - len(suffix) >= 2 and word[:-len(suffix)] in _UNITS:
                return _UNITS[word[:-len(suffix)]]
        return None

    @staticmethod
    def _number(parts, index):
        word = parts[index]
        if word.isdigit():
            return int(word), index + 1
        value = _NUMBER_WORDS.get(word)
        if value is None:
            return None, index
        if value >= 10 and value % 10 == 0 and index + 1 < len(parts):
            ones = _NUMBER_WORDS.get(parts[index + 1])
            if ones is not None and 1 <= ones <= 9:
                return value + ones, index + 2
        return value, index + 1

    def _window_at(self, parts, index):
        word = parts[index]
        if word == "ytd":
            return 1, (0, "ytd")
        if word in _LAST_SHORT:
            return 1, (1, _LAST_SHORT[word])
        cursor, prefix = index, None
        if word in _WINDOW_PREFIX:
            prefix, cursor = word, index + 1
            if cursor >= len(parts):
                return None
        number, after = self._number(parts, cursor)
        if number is not None:
            if after < len(parts):
                unit = self._unit(parts[after], parts[cursor].isdigit())
                if unit:
                    return after + 1 - index, (number, unit)
            return None
        if prefix in ("son", "last", "past", "previous"):
            unit = self._unit(parts[cursor], False)
            if unit:
                return cursor + 1 - index, (1, unit)
        return None

    def window(self, text):
        parts = _words(text)
        found, index = [], 0
        while index < len(parts):
            match = self._window_at(parts, index)
            if match:
                found.append(match[1])
                index += match[0]
            else:
                index += 1
        return found

    # --- tokenlar ---
    def _phrase_at(self, parts, index):
        limit = min(self._max_phrase, len(parts) - index)
        for size in range(limit, 1, -1):
            chunk = parts[index:index + size]
            canon = self._phrase_raw.get(tuple(chunk))
            if canon is None:
                canon = self._phrase_stem.get(tuple(self._pstem(w) for w in chunk))
            if canon is not None:
                return size, canon
        return None

    def tokens(self, text):
        parts = _words(text)
        out, index = [], 0
        while index < len(parts):
            match = self._window_at(parts, index)
            if match:
                number, unit = match[1]
                out.append("win:%d:%s" % (number, unit))
                index += match[0]
                continue
            phrase = self._phrase_at(parts, index) if self._max_phrase > 1 else None
            if phrase:
                size, canon = phrase
            else:
                size, canon = 1, self._norm_word(parts[index])
            out.extend(self._expansions.get(canon, (canon,)))
            index += size
        return out

    def is_generic(self, token):
        return fold(token) in self._generic

    def is_structural(self, token):
        """Süre penceresi, ölçü, yön ya da birleştirme tokenı mı (konu kelimesi değil)."""
        token = str(token or "")
        return token.startswith("win:") or fold(token) in self._structural

    def describe(self, name):
        out = []
        for token in self.tokens(name):
            if token.startswith("win:"):
                _, number, unit = token.split(":")
                text = "yıl başından beri" if unit == "ytd" else "son %s %s" % (number, _WINDOW_TEXT[unit])
            else:
                text = token.replace("_", " ")
            if text not in out:
                out.append(text)
        return " ".join(out)

    def with_mined(self, pairs):
        groups = []
        for short, word in (pairs or {}).items():
            short, word = _phrase(short), _phrase(word)
            if short and word and short != word:
                groups.append([word, short])
        extra = {"groups": groups, "expansions": {}, "generic": [], "suffixes": [], "structural": []}
        return Lexicon(_merge(self._data, extra))


def _subsequence(short, word):
    position = 0
    for ch in short:
        position = word.find(ch, position) + 1
        if position == 0:
            return False
    return True


def mine_abbreviations(records, min_support=2, min_confidence=0.6, lexicon=None):
    """Yorumlu kolonlardan {kısaltma: açılım} çıkarır; sözlükte olmayan 2-5 harfli parçalar aday."""
    lex = lexicon if lexicon is not None else load()
    appearances, pairs = {}, {}
    for record in records:
        name, comment = record.get("column"), record.get("column_comment")
        if not name or not comment:
            continue
        shorts = set(p for p in _words(name)
                     if 2 <= len(p) <= 5 and p.isalpha() and p != "ytd" and not lex.known(p))
        if not shorts:
            continue
        words = set()
        for word in _words(comment):
            if len(word) < 4 or not word.isalpha():
                continue
            canon = lex._norm_word(word)
            if canon in lex._generic and canon not in lex._group_canons:
                continue
            words.add(canon)
        for short in shorts:
            appearances[short] = appearances.get(short, 0) + 1
            for word in words:
                if (len(short) < len(word) and short[0] == word[0] and _subsequence(short, word)):
                    pairs[(short, word)] = pairs.get((short, word), 0) + 1
    best = {}
    for (short, word), count in pairs.items():
        current = best.get(short)
        if current is None or (-count, word) < (-current[1], current[0]):
            best[short] = (word, count)
    mined = {}
    for short, (word, count) in best.items():
        if count >= min_support and count / float(appearances[short]) >= min_confidence:
            mined[short] = word
    return mined


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        raise ConfigError("Sözlük dosyası okunamadı ya da geçerli JSON değil: %s" % os.path.basename(str(path)))
    return _normalize_data(data)


def load(path=None, extra_path=None):
    global _DEFAULT
    if path is None:
        if _DEFAULT is None:
            _DEFAULT = Lexicon(_read(_DEFAULT_PATH))
        base = _DEFAULT
        if extra_path is None:
            return base
        return Lexicon(_merge(base._data, _read(extra_path)))
    data = _read(path)
    if extra_path is not None:
        data = _merge(data, _read(extra_path))
    return Lexicon(data)
