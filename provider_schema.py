"""Shared full Provider input schema. Secret values are commands, never public settings."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
VOLCENGINE_DEFAULT_PROJECT_NAME = 'default'
VOLCENGINE_DEFAULT_REGION = 'cn-beijing'
NETWORK_PROTOCOLS = frozenset({'openai', 'apimart', 'gemini', 'volcengine', 'runninghub', 'kie'})
SECRET_FIELDS = {'api_key': 'clear_key', 'wallet_api_key': 'clear_wallet_key',
                 'volcengine_access_key_id': 'clear_volcengine_access_key_id',
                 'volcengine_secret_access_key': 'clear_volcengine_secret_access_key'}
class ApiProviderPayload(BaseModel):
    id: str = ""
    name: str = ""
    base_url: str = ""
    protocol: str = "openai"
    image_request_mode: str = "openai"
    image_edit_route: str = "general"
    image_generation_endpoint: str = ""
    image_edit_endpoint: str = ""
    enabled: bool = True
    primary: bool = False
    image_models: List[str] = []
    chat_models: List[str] = []
    video_models: List[str] = []
    model_names: Dict[str, str] = {}
    model_protocols: Dict[str, str] = {}
    ms_loras: List[Dict[str, Any]] = []
    ms_defaults_version: int = 0
    rh_apps: List[Dict[str, Any]] = []
    rh_workflows: List[Dict[str, Any]] = []
    volcengine_project_name: str = VOLCENGINE_DEFAULT_PROJECT_NAME
    volcengine_region: str = VOLCENGINE_DEFAULT_REGION
    volcengine_access_key_id: Optional[str] = None
    volcengine_secret_access_key: Optional[str] = None
    api_key: Optional[str] = None
    wallet_api_key: Optional[str] = None
    clear_key: bool = False
    clear_wallet_key: bool = False
    clear_volcengine_access_key_id: bool = False
    clear_volcengine_secret_access_key: bool = False
