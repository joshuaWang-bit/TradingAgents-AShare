"""Plugin loader - automatically registers built-in and custom plugins."""

import logging
import os
from pathlib import Path
from typing import List, Optional

from .registry import DataSourceRegistry

logger = logging.getLogger(__name__)


def register_builtin_plugins() -> None:
    """Register all built-in data source plugins."""
    from .builtin import AkshareDataSource, PreloadedDataSource, SmartCacheDataSource, TushareDataSource
    
    # Register AkShare as default
    DataSourceRegistry.register("akshare", AkshareDataSource, set_default=True)
    
    # Register preloaded source (full market preload)
    DataSourceRegistry.register("preloaded", PreloadedDataSource)
    
    # Register smart cache source (on-demand with LRU eviction)
    DataSourceRegistry.register("smart_cache", SmartCacheDataSource)
    
    # Register Tushare source
    DataSourceRegistry.register("tushare", TushareDataSource)
    
    logger.info("[PluginLoader] Registered built-in plugins: akshare, preloaded, smart_cache, tushare")


def load_custom_plugins(plugin_paths: List[str] = None) -> None:
    """Load custom plugins from specified paths.
    
    Custom plugins should be Python files containing a DataSource subclass.
    
    Args:
        plugin_paths: List of paths to plugin directories or files
    """
    import importlib.util
    import sys
    from pathlib import Path
    
    paths = plugin_paths or []
    
    # Also check environment variable
    env_paths = os.getenv("TA_DATA_SOURCE_PLUGINS", "")
    if env_paths:
        paths.extend(env_paths.split(os.pathsep))
    
    for path_str in paths:
        path = Path(path_str)
        if not path.exists():
            logger.warning(f"[PluginLoader] Plugin path not found: {path}")
            continue
        
        if path.is_file() and path.suffix == ".py":
            # Load single file
            _load_plugin_file(path)
        elif path.is_dir():
            # Load all .py files in directory
            for plugin_file in path.glob("*.py"):
                if plugin_file.name.startswith("_"):
                    continue
                _load_plugin_file(plugin_file)


def _load_plugin_file(file_path: Path) -> None:
    """Load a single plugin file."""
    import importlib.util
    
    try:
        spec = importlib.util.spec_from_file_location(
            file_path.stem, 
            file_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Find DataSource subclasses in the module
        from .base import DataSource
        
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and 
                issubclass(attr, DataSource) and 
                attr is not DataSource and
                not attr_name.startswith("_")):
                
                # Create instance to get name
                try:
                    temp_instance = attr()
                    name = temp_instance.name
                    DataSourceRegistry.register(name, attr)
                    logger.info(f"[PluginLoader] Loaded custom plugin: {name} from {file_path}")
                except Exception as e:
                    logger.error(f"[PluginLoader] Failed to load plugin from {file_path}: {e}")
        
    except Exception as e:
        logger.error(f"[PluginLoader] Failed to import {file_path}: {e}")


def initialize_plugins() -> None:
    """Initialize the plugin system.
    
    This should be called once at application startup.
    """
    # Register built-in plugins
    register_builtin_plugins()
    
    # Load custom plugins
    load_custom_plugins()
    
    # Set default from environment if specified
    default_source = os.getenv("TA_DATA_SOURCE")
    if default_source:
        if DataSourceRegistry.set_default(default_source):
            logger.info(f"[PluginLoader] Default data source set from env: {default_source}")
        else:
            logger.warning(f"[PluginLoader] Invalid default data source from env: {default_source}")
    
    logger.info(f"[PluginLoader] Available data sources: {DataSourceRegistry.list_sources()}")
