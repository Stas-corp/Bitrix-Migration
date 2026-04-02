import re
from datetime import datetime
from typing import Optional

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator


def _clean_str(v):
    """Convert empty strings and 'NULL' to None."""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if v in ('', 'NULL', 'null'):
            return None
    return v


def _to_datetime(v):
    """Normalize MySQL datetime to Python datetime."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        v = v.strip()
        if not v or v in ('NULL', 'null', '0000-00-00 00:00:00'):
            return None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.strptime(v, fmt)
            except ValueError:
                continue
    return None


def _to_int_or_none(v):
    """Convert 0, empty, 'NULL' to None, otherwise int."""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if v in ('', 'NULL', 'null', '0'):
            return None
        try:
            v = int(v)
        except ValueError:
            return None
    if isinstance(v, (int, float)):
        return int(v) if v else None
    return None


class BitrixProject(BaseModel):
    external_id: int
    name: str
    type: str  # 'project' or 'workgroup'
    closed: bool = False
    owner_bitrix_id: Optional[int] = None
    tags: Optional[str] = None
    date_start: Optional[datetime] = None
    date_end: Optional[datetime] = None
    description: Optional[str] = None

    @field_validator('name', mode='before')
    @classmethod
    def clean_name(cls, v):
        return _clean_str(v) or 'Untitled'

    @field_validator('closed', mode='before')
    @classmethod
    def parse_closed(cls, v):
        if isinstance(v, str):
            return v.upper() in ('Y', 'YES', '1', 'TRUE')
        return bool(v)

    @field_validator('type', mode='before')
    @classmethod
    def parse_type(cls, v):
        if isinstance(v, str) and v.upper() == 'Y':
            return 'project'
        if v in ('project', 'workgroup'):
            return v
        return 'workgroup'

    @field_validator('tags', 'description', mode='before')
    @classmethod
    def clean_optional_str(cls, v):
        return _clean_str(v)

    @field_validator('owner_bitrix_id', mode='before')
    @classmethod
    def clean_owner(cls, v):
        return _to_int_or_none(v)

    @field_validator('date_start', 'date_end', mode='before')
    @classmethod
    def clean_dates(cls, v):
        return _to_datetime(v)


class BitrixTask(BaseModel):
    external_id: int
    name: str
    project_external_id: Optional[int] = None
    responsible_user_ids: Optional[str] = None
    auditor_user_ids: Optional[str] = None
    tags: Optional[str] = None
    date_deadline: Optional[datetime] = None
    date_created: Optional[datetime] = None
    description: Optional[str] = None
    stage_id: Optional[int] = None
    parent_id: Optional[int] = None
    creator_bitrix_id: Optional[int] = None

    @field_validator('name', mode='before')
    @classmethod
    def clean_name(cls, v):
        return _clean_str(v) or 'Untitled Task'

    @field_validator('project_external_id', 'stage_id', 'parent_id', 'creator_bitrix_id', mode='before')
    @classmethod
    def clean_int(cls, v):
        return _to_int_or_none(v)

    @field_validator('responsible_user_ids', 'auditor_user_ids', 'tags', 'description', mode='before')
    @classmethod
    def clean_str(cls, v):
        return _clean_str(v)

    @field_validator('date_deadline', 'date_created', mode='before')
    @classmethod
    def clean_date(cls, v):
        return _to_datetime(v)


class BitrixStage(BaseModel):
    id: int
    name: str
    entity_type: str = 'G'
    entity_id: int = 0

    @field_validator('name', mode='before')
    @classmethod
    def clean_name(cls, v):
        return _clean_str(v) or 'Untitled Stage'

    @field_validator('entity_type', mode='before')
    @classmethod
    def clean_entity_type(cls, v):
        val = (_clean_str(v) or 'G').upper()
        return val if val in ('G', 'U') else 'G'


class BitrixTag(BaseModel):
    id: int
    name: str
    source: Optional[str] = 'task'

    @field_validator('name', mode='before')
    @classmethod
    def clean_name(cls, v):
        return _clean_str(v) or ''


class BitrixComment(BaseModel):
    message_id: int
    document_model: str = 'project.task'
    entity_id: int
    type: str = 'comment'
    body: Optional[str] = None
    date: Optional[datetime] = None
    author_bitrix_id: int = 0

    @field_validator('body', mode='before')
    @classmethod
    def clean_body(cls, v):
        return _clean_str(v)

    @field_validator('date', mode='before')
    @classmethod
    def clean_date(cls, v):
        return _to_datetime(v)

    @field_validator('author_bitrix_id', mode='before')
    @classmethod
    def clean_author(cls, v):
        return int(v) if v else 0


def parse_php_int_array(value: str) -> list:
    """Parse PHP serialized int array.

    Handles format: a:1:{i:0;s:2:"42";}  →  [42]
    Also handles:   a:2:{i:0;i:5;i:1;i:10;}  →  [5, 10]
    """
    if not value or not str(value).startswith('a:'):
        return []
    # String values: s:N:"digits";
    result = [int(m) for m in re.findall(r's:\d+:"(\d+)";', value)]
    if result:
        return result
    # Integer values — keys are 0,1,2... values follow (every other, starting at index 1)
    all_ints = re.findall(r'i:(\d+);', value)
    return [int(v) for v in all_ints[1::2]]


class BitrixDepartment(BaseModel):
    dept_id: int
    dept_name: str
    parent_dept_id: Optional[int] = None
    head_user_id: Optional[int] = None
    depth_level: int = 0

    @field_validator('dept_name', mode='before')
    @classmethod
    def clean_name(cls, v):
        return _clean_str(v) or 'Untitled Department'

    @field_validator('parent_dept_id', 'head_user_id', mode='before')
    @classmethod
    def clean_int(cls, v):
        return _to_int_or_none(v)

    @field_validator('depth_level', mode='before')
    @classmethod
    def clean_depth(cls, v):
        return int(v) if v else 0


class BitrixEmployee(BaseModel):
    user_id: int
    login: str = ''
    full_name: str
    email: Optional[str] = None
    dept_ids: list = Field(default_factory=list)
    work_phone: Optional[str] = None
    mobile_phone: Optional[str] = None
    personal_phone: Optional[str] = None
    telegram: Optional[str] = None  # заполняется отдельно в загрузчике

    @model_validator(mode='before')
    @classmethod
    def parse_raw_dept(cls, data):
        raw = data.get('raw_dept') or ''
        data['dept_ids'] = parse_php_int_array(str(raw)) if raw else []
        return data

    @field_validator('full_name', mode='before')
    @classmethod
    def clean_name(cls, v):
        return _clean_str(v) or 'Unknown Employee'

    @field_validator('email', 'work_phone', 'mobile_phone', 'personal_phone', 'telegram',
                     mode='before')
    @classmethod
    def clean_contact(cls, v):
        return _clean_str(v)

    @field_validator('login', mode='before')
    @classmethod
    def clean_login(cls, v):
        return _clean_str(v) or ''


class BitrixAttachment(BaseModel):
    entity_type: str = 'task'  # 'task' or 'comment'
    entity_id: int = 0
    forum_message_id: Optional[int] = None
    file_name: str = ''
    file_size: int = 0
    content_type: str = 'application/octet-stream'
    file_path: str = ''
    attached_at: Optional[datetime] = None

    @field_validator('file_name', 'content_type', 'file_path', mode='before')
    @classmethod
    def clean_str(cls, v):
        return _clean_str(v) or ''

    @field_validator('forum_message_id', mode='before')
    @classmethod
    def clean_fmid(cls, v):
        return _to_int_or_none(v)

    @field_validator('attached_at', mode='before')
    @classmethod
    def clean_date(cls, v):
        return _to_datetime(v)
