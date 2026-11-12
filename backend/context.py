"""Resolve the API module at call time, without circular module initialization."""
import importlib
class ModuleProxy:
    def __init__(self,name):self.name=name
    def __getattr__(self,key):return getattr(importlib.import_module(self.name),key)
app=ModuleProxy('backend.app')
pro=ModuleProxy('backend.studio_pro')
