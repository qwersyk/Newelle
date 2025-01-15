import os 
import subprocess
from .system import get_spawn_command


class ReplaceHelper:
    DISTRO = None

    @staticmethod
    def get_distribution() -> str:
        """
        Get the distribution

        Returns:
            str: distribution name
            
        """
        if ReplaceHelper.DISTRO is None:
            try:
                ReplaceHelper.DISTRO = subprocess.check_output(get_spawn_command() + ['bash', '-c', 'lsb_release -ds']).decode('utf-8').strip()
            except subprocess.CalledProcessError:
                ReplaceHelper.DISTRO = "Unknown"
        
        return ReplaceHelper.DISTRO
    
    @staticmethod
    def get_desktop_environment() -> str:
        desktop = os.getenv("XDG_CURRENT_DESKTOP")
        if desktop is None:
            desktop = "Unknown"
        return desktop


def replace_variables(text: str) -> str:
    """
    Replace variables in prompts
    Supported variables:
        {DIR}: current directory
        {DISTRO}: distribution name
        {DE}: desktop environment

    Args:
        text: text of the prompt

    Returns:
        str: text with replaced variables
    """
    text = text.replace("{DIR}", os.getcwd())
    if "{DISTRO}" in text:
        text = text.replace("{DISTRO}", ReplaceHelper.get_distribution())
    if "{DE}" in text:
        text = text.replace("{DE}", ReplaceHelper.get_desktop_environment())
    return text
