import json
import os
import re
from typing import Dict, Any


class I18nService:
    _instance = None
    _locales: Dict[str, Dict[str, Any]] = {}
    _cache: Dict[str, Dict[str, str]] = {}  # Flattened cache per language
    _last_mtimes: Dict[str, float] = {}  # Store last modification time

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(I18nService, cls).__new__(cls)
            cls._instance._load_locales()
        return cls._instance

    def _load_locales(self):
        """
        Load all JSON files from app/locales directory and build flattened cache
        """
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        locales_dir = os.path.join(base_dir, "locales")

        if not os.path.exists(locales_dir):
            return

        for filename in os.listdir(locales_dir):
            if filename.endswith(".json"):
                lang = filename.split(".")[0]
                filepath = os.path.join(locales_dir, filename)
                try:
                    # Update mtime
                    mtime = os.path.getmtime(filepath)
                    self._last_mtimes[lang] = mtime

                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self._locales[lang] = data
                        self._cache[lang] = self._build_flattened_cache(data)
                except Exception as e:
                    print(f"Error loading locale {filename}: {e}")

    def _reload_if_changed(self):
        """
        Check if any locale file has changed and reload if necessary
        """
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        locales_dir = os.path.join(base_dir, "locales")

        if not os.path.exists(locales_dir):
            return

        should_reload = False
        for filename in os.listdir(locales_dir):
            if filename.endswith(".json"):
                lang = filename.split(".")[0]
                filepath = os.path.join(locales_dir, filename)
                try:
                    mtime = os.path.getmtime(filepath)
                    if lang not in self._last_mtimes or mtime > self._last_mtimes[lang]:
                        should_reload = True
                        break
                except OSError:
                    continue

        if should_reload:
            # print("Reloading locales due to file change...")
            self._load_locales()

    def _build_flattened_cache(self, data: Dict[str, Any]) -> Dict[str, str]:
        """
        Recursively flatten the dictionary.
        Stores both "parent.child": val and "child": val.
        Leaf key collisions: First encountered wins (or overwrite, depends on traversal).
        """
        cache = {}

        def recurse(current_data, prefix=""):
            if isinstance(current_data, dict):
                for k, v in current_data.items():
                    full_key = f"{prefix}.{k}" if prefix else k
                    recurse(v, full_key)
            else:
                # Store full path
                cache[prefix] = str(current_data)
                # Store leaf key (if likely unique or acceptable fallback)
                leaf_key = prefix.split(".")[-1]
                if leaf_key not in cache:
                    cache[leaf_key] = str(current_data)

        recurse(data)
        return cache

    def get_locale(self, lang: str = None) -> Dict[str, Any]:
        """
        Get the full translation dictionary for a specific language.
        If lang is None, use settings.SYSTEM_LANGUAGE.
        """
        from app.core.config import settings
        target_lang = lang or settings.SYSTEM_LANGUAGE
        self._reload_if_changed()  # Check for changes before returning
        return self._locales.get(target_lang.lower(), self._locales.get('zh', {}))

    def get(self, key: str, default: Any = None, lang: str = None) -> str:
        """
        Get translation by key.
        Supports both full path (e.g. "stock_basic.pe_ttm") and leaf key (e.g. "pe_ttm").
        If lang is None, use settings.SYSTEM_LANGUAGE.
        """
        from app.core.config import settings
        target_lang = lang or settings.SYSTEM_LANGUAGE
        
        # For performance, maybe don't check reload on every single key access if frequent?
        # But 'get' is less used than 'get_locale' (bulk load).
        # Let's check anyway to be safe, or assume bulk load is primary.
        # Given this is likely used for backend validation messages, it's fine.
        self._reload_if_changed()
        lang_cache = self._cache.get(target_lang.lower(), self._cache.get('zh', {}))
        return lang_cache.get(key, default)

    def _instantiate_params(self, text: str, params: Dict[str, Any]) -> str:
        if not params:
            return text

        # Normalize legacy {{name}} placeholders to Python format-style {name}.
        normalized = re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", r"{\1}", text)

        class _SafeFormatDict(dict):
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"

        return normalized.format_map(_SafeFormatDict(params))

    def t(self, key: str, **kwargs: Any) -> str:
        """
        Translate using the configured system language and instantiate parameters.
        """
        template = self.get(key, key)
        return self._instantiate_params(template, kwargs)


i18n_service = I18nService()
