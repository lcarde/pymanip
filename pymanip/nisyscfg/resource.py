import warnings

from .session import NISysCfgSession
from ._lib.session import NISysCfgFindHardware, NISysCfgCloseHandle, NISysCfgNextResource
from ._lib.properties import NISysCfgGetResourceProperty, NISysCfgGetResourceIndexedProperty
from ._lib.constants import NISysCfgResourceProperty

class NISysCfgResource:

    def __init__(self, resourceHandle):
        self.resourceHandle = resourceHandle
        
    def close(self):
        if self.resourceHandle:
            status = NISysCfgCloseHandle(self.resourceHandle)
            if status != 0:
                raise RuntimeError('NISysCfgCloseHandle failed.')
            self.resourceHandle = None
        else:
            warnings.warn('close was called twice on NISysCfgResource!')
        
    def __del__(self):
        if self.resourceHandle is not None:
            warnings.warn('NISysCfgResource was left open!', RuntimeWarning)
            self.close()
            
    def __getattr__(self, key):
        status, val = NISysCfgGetResourceProperty(self.resourceHandle, 
                                                  getattr(NISysCfgResourceProperty, key))
        if status == 0:
            return val
        raise AttributeError(f'NISysCfgGetResourceProperty returned {status:}')
    
            
class _NISysCfgHardwareEnumerator:
    
    def __init__(self, sesn):
        self.sesn = sesn
        self.resourceEnumHandle = None
        
    def __enter__(self):
        status, resourceEnumHandle = NISysCfgFindHardware(self.sesn.sessionHandle)
        if status != 0:
            raise RuntimeError('NISysCfgFindHardware failed.')
        self.resourceEnumHandle = resourceEnumHandle
        return self
        
    def __exit__(self, type_, value, cb):
        if self.resourceEnumHandle:
            status = NISysCfgCloseHandle(self.resourceEnumHandle)
            if status != 0:
                raise RuntimeError('NISysCfgCloseHandle failed.')
                
    def next_resource(self):
        status, resourceHandle = NISysCfgNextResource(self.sesn.sessionHandle, self.resourceEnumHandle)
        if status == 0 and resourceHandle:
            return NISysCfgResource(resourceHandle)
        else:
            return None

            
def NISysCfgHardwareEnumerator():
    with NISysCfgSession() as sesn, \
         _NISysCfgHardwareEnumerator(sesn) as enum:
         
        while True:
            res = enum.next_resource()
            if res:
                yield res
            else:
                break