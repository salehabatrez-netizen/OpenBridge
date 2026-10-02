"""Multi-project registry for handoff documents.

Why this exists
---------------
One workspace holds more than one project, and the user assigns an assistant
to a specific one. Without separation every assistant reads every project's
notes, and the Minecraft agent starts "helpfully" acting on a web-scraper
task. Worse, an assistant that is told nothing silently picks up whatever
state file it finds first.

Layout::

    HANDOFF/
        registry.json            projects + which one is active
        SPEC.md  README.md
        projects/
            <slug>/state.json    one handoff document per project
            <slug>/HANDOFF.md

Every read and write goes through a slug, and the registry records which slug
is currently assigned. An assistant that ignores the slug cannot accidentally
write into a sibling project, because the path is derived from the slug and
validated.
"""

import json
import os
import re
import tempfile
import time

REGISTRY_VERSION = 1
# Conservative on purpose: the slug becomes a directory name on Windows.
SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,47}$')
RESERVED = {'con', 'prn', 'aux', 'nul', 'projects', 'registry'}


def slugify(text):
    """Best-effort slug. Raises when nothing usable survives."""
    lowered = (text or '').strip().lower()
    lowered = re.sub(r'[\s/\\.]+', '-', lowered)
    lowered = re.sub(r'[^a-z0-9_-]', '', lowered)
    lowered = re.sub(r'-{2,}', '-', lowered).strip('-_')
    if not lowered:
        raise ValueError('无法从 %r 生成合法的项目标识' % text)
    return lowered[:48]


def validate_slug(slug):
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise ValueError(
            '项目标识只能是小写字母/数字/连字符/下划线，1-48 字符，且以字母或数字开头：%r' % slug)
    if slug in RESERVED:
        raise ValueError('项目标识 %r 是保留名' % slug)
    return slug


def _atomic_write_json(path, payload):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        'w', encoding='utf-8', dir=parent or None,
        prefix='.registry-', suffix='.tmp', delete=False)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


class Registry:
    """The list of projects plus the one currently assigned."""

    def __init__(self, root):
        self.root = str(root)
        self.path = os.path.join(self.root, 'registry.json')
        self.data = {'schema': REGISTRY_VERSION, 'active': None, 'projects': {}}

    # ---------------------------------------------------------------- io
    def load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as handle:
                loaded = json.load(handle)
        except (OSError, ValueError):
            return self.data
        if isinstance(loaded, dict):
            self.data['schema'] = loaded.get('schema', REGISTRY_VERSION)
            self.data['active'] = loaded.get('active')
            projects = loaded.get('projects')
            self.data['projects'] = projects if isinstance(projects, dict) else {}
        # An active slug pointing at a deleted project would make every later
        # call fail; drop it instead.
        if self.data['active'] not in self.data['projects']:
            self.data['active'] = None
        return self.data

    def save(self):
        _atomic_write_json(self.path, self.data)
        return self.path

    # ---------------------------------------------------------- projects
    def project_dir(self, slug):
        validate_slug(slug)
        return os.path.join(self.root, 'projects', slug)

    def state_path(self, slug):
        return os.path.join(self.project_dir(slug), 'state.json')

    def doc_path(self, slug):
        return os.path.join(self.project_dir(slug), 'HANDOFF.md')

    def exists(self, slug):
        return slug in self.data['projects']

    def register(self, slug, name='', goal=''):
        validate_slug(slug)
        if slug in self.data['projects']:
            raise ValueError('项目 %r 已存在' % slug)
        self.data['projects'][slug] = {
            'name': name or slug, 'goal': goal, 'created_at': time.time()}
        os.makedirs(self.project_dir(slug), exist_ok=True)
        if self.data['active'] is None:
            self.data['active'] = slug
        return self.data['projects'][slug]

    def remove(self, slug):
        """Forget a project. Files are left on disk on purpose - deleting a
        colleague's work because of a typo is unrecoverable."""
        if slug not in self.data['projects']:
            raise KeyError(slug)
        del self.data['projects'][slug]
        if self.data['active'] == slug:
            self.data['active'] = next(iter(self.data['projects']), None)
        return slug

    def set_active(self, slug):
        if slug not in self.data['projects']:
            raise KeyError(slug)
        self.data['active'] = slug
        return slug

    @property
    def active(self):
        return self.data.get('active')

    def resolve(self, slug=None):
        """Which project a command should act on.

        Explicit slug wins; otherwise the assigned one. Refusing when neither
        exists is deliberate: guessing is how projects get cross-contaminated.
        """
        if slug:
            validate_slug(slug)
            if slug not in self.data['projects']:
                raise KeyError(
                    '未注册的项目 %r。先运行：handoff_cli.py projects add %s' % (slug, slug))
            return slug
        if self.data.get('active'):
            return self.data['active']
        raise RuntimeError(
            '没有指定项目，也没有已指派的项目。\n'
            '  查看： python handoff_cli.py projects\n'
            '  指派： python handoff_cli.py use <项目标识>\n'
            '  新建： python handoff_cli.py projects add <项目标识> --name "名称"')

    def listing(self):
        rows = []
        for slug, meta in sorted(self.data['projects'].items()):
            rows.append({'slug': slug,
                         'name': meta.get('name', slug),
                         'goal': meta.get('goal', ''),
                         'active': slug == self.data.get('active'),
                         'created_at': meta.get('created_at', 0)})
        return rows


def load(root):
    registry = Registry(root)
    registry.load()
    return registry
