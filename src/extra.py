import importlib, subprocess, functools


def human_readable_size(size: float, decimal_places:int =2) -> str:
    size = int(size)
    unit = ''
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']:
        if size < 1024.0 or unit == 'PiB':
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"


def find_module(full_module_name):
    """
    Returns module object if module `full_module_name` can be imported.

    Returns None if module does not exist.

    Exception is raised if (existing) module raises exception during its import.
    """
    if full_module_name == "git+https://github.com/openai/whisper.git":
        full_module_name = "whisper"
    try:
        return importlib.import_module(full_module_name)
    except ImportError as exc:
        if not (full_module_name + '.').startswith(exc.name + '.'):
            raise


def install_module(module, path):
    r = subprocess.check_output(["pip3", "install", "--target", path, module]).decode("utf-8")
    return r

def can_escape_sandbox():
    try:
        r = subprocess.check_output(["flatpak-spawn", "--host", "echo", "test"])
    except subprocess.CalledProcessError as e:
        return False
    return True

def override_prompts(override_setting, PROMPTS):
    prompt_list = {}
    for prompt in PROMPTS:
        if prompt in override_setting:
            prompt_list[prompt] = override_setting[prompt]
        else:
            prompt_list[prompt] = PROMPTS[prompt]
    return prompt_list


def force_async(fn):
    '''
    turns a sync function to async function using threads
    '''
    from concurrent.futures import ThreadPoolExecutor
    import asyncio
    pool = ThreadPoolExecutor()

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        future = pool.submit(fn, *args, **kwargs)
        return asyncio.wrap_future(future)  # make it awaitable

    return wrapper


def force_sync(fn):
    '''
    turn an async function to sync function
    '''
    import asyncio

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        res = fn(*args, **kwargs)
        if asyncio.iscoroutine(res):
            return asyncio.get_event_loop().run_until_complete(res)
        return res

    return wrapper
