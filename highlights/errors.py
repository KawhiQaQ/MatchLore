"""Stable public errors; unexpected exceptions never expose source data."""
import uuid

class AppError(ValueError):
    def __init__(self,code,message,status=400):
        super().__init__(message);self.code=code;self.status=status

def error_document(error,request_id=None):
    if isinstance(error,AppError):code,message=error.code,str(error)
    elif isinstance(error,FileNotFoundError):code,message='file_not_found','Required input or data file was not found'
    elif isinstance(error,PermissionError):code,message='permission_denied','Cannot access the requested file or directory'
    elif isinstance(error,(ValueError,KeyError,TypeError)):code,message='invalid_request',str(error)
    else:code,message='internal_error','Internal operation failed'
    return dict(error=dict(code=code,message=message),request_id=request_id or str(uuid.uuid4()))
