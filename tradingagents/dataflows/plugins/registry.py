"""Data source plugin registry.

Manages registration and retrieval of data source plugins.
"""

import os
import logging
from typing import Dict, List, Optional, Type, Any

from .base import DataSource, DataSourceConfig

logger = logging.getLogger(__name__)


class DataSourceRegistry:
    """Registry for data source plugins.
    
    Usage:
        # Register a custom data source
        DataSourceRegistry.register("my_source", MyDataSource)
        
        # Get a data source instance
        source = DataSourceRegistry.get_source("my_source")
        
        # List available sources
        sources = DataSourceRegistry.list_sources()
    """
    
    _sources: Dict[str, Type[DataSource]] = {}
    _instances: Dict[str, DataSource] = {}
    _default_source: Optional[str] = None
    
    @classmethod
    def register(cls, name: str, source_class: Type[DataSource], 
                 set_default: bool = False) -> None:
        """Register a data source plugin.
        
        Args:
            name: Unique name for this data source
            source_class: Class inheriting from DataSource
            set_default: Whether to set this as the default source
        """
        if not issubclass(source_class, DataSource):
            raise ValueError(f"Source class must inherit from DataSource: {source_class}")
        
        cls._sources[name] = source_class
        logger.info(f"[DataSourceRegistry] Registered data source: {name}")
        
        if set_default or cls._default_source is None:
            cls._default_source = name
            logger.info(f"[DataSourceRegistry] Set default source: {name}")
    
    @classmethod
    def unregister(cls, name: str) -> None:
        """Unregister a data source."""
        if name in cls._sources:
            del cls._sources[name]
            cls._instances.pop(name, None)
            
            if cls._default_source == name:
                cls._default_source = next(iter(cls._sources.keys()), None)
            
            logger.info(f"[DataSourceRegistry] Unregistered data source: {name}")
    
    @classmethod
    def get_source(cls, name: Optional[str] = None, 
                   config: Optional[DataSourceConfig] = None) -> Optional[DataSource]:
        """Get a data source instance.
        
        Args:
            name: Name of the data source, or None for default
            config: Optional configuration for the data source
            
        Returns:
            DataSource instance or None if not found
        """
        # Determine which source to use
        source_name = name or cls._get_default_source_name()
        
        if source_name not in cls._sources:
            logger.error(f"[DataSourceRegistry] Data source not found: {source_name}")
            return None
        
        # Return cached instance if available and no new config
        if config is None and source_name in cls._instances:
            return cls._instances[source_name]
        
        # Create new instance
        source_class = cls._sources[source_name]
        if config is None:
            config = DataSourceConfig(name=source_name)
        
        try:
            instance = source_class(config)
            instance.initialize()
            
            # Cache instance if no custom config
            if name is None and config == DataSourceConfig(name=source_name):
                cls._instances[source_name] = instance
            
            return instance
        except Exception as e:
            logger.error(f"[DataSourceRegistry] Failed to create instance of {source_name}: {e}")
            return None
    
    @classmethod
    def get_source_with_fallback(cls, name: Optional[str] = None,
                                  config: Optional[DataSourceConfig] = None,
                                  fallback_chain: Optional[List[str]] = None) -> Optional[DataSource]:
        """Get a data source with fallback chain.
        
        Args:
            name: Primary data source name
            config: Configuration for primary source
            fallback_chain: List of fallback source names to try
            
        Returns:
            DataSource instance or None if all fail
        """
        # Try primary source
        source = cls.get_source(name, config)
        if source is not None and source.is_available():
            return source
        
        # Try fallbacks
        chain = fallback_chain or ["preloaded", "akshare"]
        for fallback_name in chain:
            if fallback_name == name:
                continue
            
            logger.info(f"[DataSourceRegistry] Trying fallback source: {fallback_name}")
            source = cls.get_source(fallback_name)
            if source is not None and source.is_available():
                return source
        
        logger.error("[DataSourceRegistry] All data sources failed")
        return None
    
    @classmethod
    def list_sources(cls) -> List[str]:
        """List all registered data source names."""
        return list(cls._sources.keys())
    
    @classmethod
    def list_source_info(cls) -> List[Dict[str, Any]]:
        """List detailed information about all registered sources."""
        result = []
        for name, source_class in cls._sources.items():
            # Create temporary instance to get info
            try:
                temp_instance = source_class(DataSourceConfig(name=name))
                result.append({
                    "name": name,
                    "display_name": temp_instance.display_name,
                    "description": temp_instance.description,
                    "supports_preload": temp_instance.supports_preload,
                    "supports_realtime": temp_instance.supports_realtime,
                    "is_default": name == cls._default_source,
                })
            except Exception as e:
                result.append({
                    "name": name,
                    "error": str(e),
                })
        return result
    
    @classmethod
    def set_default(cls, name: str) -> bool:
        """Set the default data source."""
        if name not in cls._sources:
            logger.error(f"[DataSourceRegistry] Cannot set default: {name} not registered")
            return False
        
        cls._default_source = name
        logger.info(f"[DataSourceRegistry] Default source set to: {name}")
        return True
    
    @classmethod
    def _get_default_source_name(cls) -> str:
        """Get the default source name from config or registry."""
        # Check environment variable
        env_source = os.getenv("TA_DATA_SOURCE")
        if env_source and env_source in cls._sources:
            return env_source
        
        # Return registry default
        return cls._default_source or "akshare"
    
    @classmethod
    def clear_cache(cls) -> None:
        """Clear cached instances."""
        cls._instances.clear()
        logger.info("[DataSourceRegistry] Cleared instance cache")
    
    @classmethod
    def health_check(cls) -> Dict[str, Any]:
        """Run health check on all registered sources."""
        results = {}
        for name in cls._sources:
            source = cls.get_source(name)
            if source:
                results[name] = source.health_check()
            else:
                results[name] = {"error": "Failed to create instance"}
        return results


# Convenience function for getting the default data source
def get_data_source(name: Optional[str] = None) -> Optional[DataSource]:
    """Get a data source by name, or the default if name is None."""
    return DataSourceRegistry.get_source(name)


def get_data_source_with_fallback(
    name: Optional[str] = None,
    fallback_chain: Optional[List[str]] = None
) -> Optional[DataSource]:
    """Get data source with fallback chain."""
    return DataSourceRegistry.get_source_with_fallback(name, None, fallback_chain)
