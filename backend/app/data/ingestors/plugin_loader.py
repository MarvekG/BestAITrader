import importlib
import inspect
import pkgutil
from typing import List

import app.data.ingestors.plugins as ingestors_plugins_pkg
from app.core.logger import get_logger
from app.data.ingestors.base_ingestor import BaseIngestor

logger = get_logger(__name__)


def discover_ingestor_plugin_classes() -> List[type[BaseIngestor]]:
    """
    扫描 plugins 包并返回可用插件类。

    Returns:
        插件类列表。
    """
    discovered_classes: List[type[BaseIngestor]] = []

    for module_info in pkgutil.iter_modules(ingestors_plugins_pkg.__path__):
        module_name = module_info.name
        if not module_name.endswith("_ingestor"):
            continue

        try:
            module = importlib.import_module(f"{ingestors_plugins_pkg.__name__}.{module_name}")
        except Exception:
            logger.exception("Failed to import ingestor module: %s", module_name)
            continue

        for _, plugin_class in inspect.getmembers(module, inspect.isclass):
            if plugin_class.__module__ != module.__name__:
                continue
            if plugin_class is BaseIngestor:
                continue
            if not issubclass(plugin_class, BaseIngestor):
                continue
            if not plugin_class.get_source_name():
                continue
            discovered_classes.append(plugin_class)

    return discovered_classes


def instantiate_ingestor_plugins() -> List[BaseIngestor]:
    """
    实例化所有已发现的插件。

    Returns:
        插件实例列表。
    """
    ingestors: List[BaseIngestor] = []

    plugin_classes = discover_ingestor_plugin_classes()

    for plugin_class in plugin_classes:
        try:
            ingestors.append(plugin_class())
            logger.info(
                "Discovered ingestor plugin: source=%s class=%s",
                plugin_class.get_source_name(),
                plugin_class.__name__,
            )
        except Exception:
            logger.exception(
                "Failed to initialize ingestor plugin: source=%s class=%s",
                plugin_class.get_source_name(),
                plugin_class.__name__,
            )

    return ingestors


def validate_ingestor_registration(ingestor: BaseIngestor, existing_sources: set[str]) -> None:
    """
    校验插件注册前置条件。

    Args:
        ingestor: 插件实例。
        existing_sources: 已注册 source_name 集合。

    Raises:
        ValueError: 当 source_name 重复时抛出。
    """
    source_name = ingestor.get_source_name()
    if source_name in existing_sources:
        raise ValueError(f"Duplicate ingestor source_name: {source_name}")
