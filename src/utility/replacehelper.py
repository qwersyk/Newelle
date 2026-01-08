import os 
import subprocess
from .system import get_spawn_command
import time
from .system import is_wayland 
import re 
import random 

class ReplaceHelper:
    DISTRO = None
    controller = None

    @staticmethod
    def set_controller(controller):
        ReplaceHelper.controller = controller

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
    def gisplay_server() -> str:
        """
        Get the server

        Returns:
            str: server name
            
        """ 
        return "Wayland" if is_wayland() else "X11"

    @staticmethod
    def get_desktop_environment() -> str:
        desktop = os.getenv("XDG_CURRENT_DESKTOP")
        if desktop is None:
            desktop = "Unknown"
        return desktop

    @staticmethod
    def get_user() -> str:
        """
        Get the user

        Returns:
            str: user name
            
        """
        if ReplaceHelper.controller is None:
            return "User"
        return ReplaceHelper.controller.newelle_settings.username
    
    @staticmethod
    def get_tools_json() -> str:
        """
        Get the JSON list of tools available to the LLM
        """
        tools_settings = ReplaceHelper.controller.newelle_settings.tools_settings_dict
        enabled_tools = {}
        for tool_name, settings in tools_settings.items():
             if "enabled" in settings:
                 enabled_tools[tool_name] = settings["enabled"]
        # Link websearch setting with the search tool
        if not ReplaceHelper.controller.newelle_settings.websearch_on:
            enabled_tools["search"] = False
        return ReplaceHelper.controller.tools.get_tools_prompt(enabled_tools_dict=enabled_tools, tools_settings=tools_settings)

def replace_variables(text: str) -> str:
    """
    Replace variables in prompts
    Supported variables:
        {DIR}: current directory
        {DISTRO}: distribution name
        {DE}: desktop environment
        {USER}: user's username
        {DATE}: current date
        {TOOLS}: JSON list of tools available to the LLM

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
    if "{DATE}" in text:
        text = text.replace("{DATE}", str(time.strftime("%H:%M %Y-%m-%d")))
    if "{USER}" in text:
        text = text.replace("{USER}", ReplaceHelper.get_user())
    if "{DISPLAY}" in text:
        text = text.replace("{DISPLAY}", ReplaceHelper.gisplay_server())
    if "{TOOLS}" in text:
        text = text.replace("{TOOLS}", ReplaceHelper.get_tools_json())
    return text

def replace_variables_dict() -> dict:
    return {
        "{DIR}": os.getcwd(),
        "{DISTRO}": ReplaceHelper.get_distribution(),
        "{DE}": ReplaceHelper.get_desktop_environment(),
        "{DATE}": str(time.strftime("%H:%M %Y-%m-%d")),
        "{USER}": ReplaceHelper.get_user(),
        "{DISPLAY}": ReplaceHelper.gisplay_server(),
        "{TOOLS}": ReplaceHelper.get_tools_json(),
    }

class PromptFormatter:
    """
    A class to format dynamic prompts with variables, conditionals, and random choices.
    """

    def __init__(self, simple_vars, get_variable_func):
        """
        Initializes the PromptFormatter.

        Args:
            simple_vars (dict): A dictionary for simple variable replacements.
                                e.g., {"{DISTRO}": "Arch Linux"}
            get_variable_func (function): A function that takes a variable name (str)
                                          and returns its value. This is used for
                                          evaluating conditions.
        """
        self.simple_vars = simple_vars
        self.get_variable = get_variable_func
        # Regex to find {RANDOM:...} and {COND:...} blocks.
        # It handles nested structures by matching non-greedyly.
        self.random_block_re = re.compile(r"\{RANDOM:(.*?)\}", re.DOTALL)
        self.cond_block_re = re.compile(r"\{COND:(.*?)\}", re.DOTALL)
        # Regex to find escape sequences
        self.escape_re = re.compile(r"\\([\{\}\[\]])")


    def format(self, prompt_string):
        """
        Formats the given prompt string by processing all dynamic parts.

        The formatting process is as follows:
        1. Process all {RANDOM:...} blocks.
        2. Process all {COND:...} blocks.
        3. Replace simple variables like {USER}, {DISTRO}, etc.
        4. Handle escaped characters like \\{, \\}, \\[, \\].

        Args:
            prompt_string (str): The raw prompt string with dynamic syntax.

        Returns:
            str: The fully formatted and resolved prompt string.
        """
        # Step 1: Handle escaped characters by replacing them with a temporary placeholder
        # This prevents them from being interpreted as part of the syntax.
        escaped_map = {}
        def escape_handler(match):
            char = match.group(1)
            placeholder = f"__ESCAPED_{ord(char)}__"
            escaped_map[placeholder] = char
            return placeholder

        processed_prompt = self.escape_re.sub(escape_handler, prompt_string)

        # Step 2: Iteratively process RANDOM and COND blocks until none are left.
        # This allows for nesting, e.g., a COND block inside a RANDOM choice.
        while self.random_block_re.search(processed_prompt) or self.cond_block_re.search(processed_prompt):
            processed_prompt = self.random_block_re.sub(self._process_random_match, processed_prompt)
            processed_prompt = self.cond_block_re.sub(self._process_cond_match, processed_prompt)

        # Step 3: Replace simple variables
        for var, value in self.simple_vars.items():
            processed_prompt = processed_prompt.replace(var, str(value))

        # Step 4: Restore escaped characters
        for placeholder, char in escaped_map.items():
            processed_prompt = processed_prompt.replace(placeholder, char)

        return processed_prompt.strip()


    def _process_random_match(self, match):
        """Callback function for re.sub to handle a single {RANDOM:...} block."""
        content = match.group(1).strip()
        lines = content.split('\n')
        
        choices = []
        for line in lines:
            if not line.strip():
                continue

            # Check for probability/weight part: e.g., "[? 0.7]" or "[? 3]"
            prob_match = re.match(r"\[\?\s*([0-9.]+)\s*\](.*)", line, re.DOTALL)
            if prob_match:
                weight = float(prob_match.group(1))
                text = prob_match.group(2).strip()
                choices.append((text, weight))
            else:
                # If no probability is specified, assume a weight of 1
                choices.append((line.strip(), 1.0))

        # Separate the prompts and their weights for random.choices
        prompts = [c[0] for c in choices]
        weights = [c[1] for c in choices]

        if not prompts:
            return ""
            
        # Select one prompt based on the specified weights
        chosen_prompt = random.choices(prompts, weights=weights, k=1)[0]
        
        return chosen_prompt

    def _process_cond_match(self, match):
        """
        Callback for re.sub to handle a {COND:...} block.
        This function correctly parses conditions that have multi-line prompts.
        A prompt is associated with the condition that precedes it.
        """
        content = match.group(1) # Don't strip content here, preserve indentation
        lines = content.split('\n')

        # A list to hold structures of (condition_string, prompt_text)
        parsed_conditions = []
        current_prompt_lines = []
        current_condition = None

        # Regex to find a line starting with a condition, allowing for leading whitespace.
        condition_line_re = re.compile(r"^\s*\[(.*?)\](.*)")

        for line in lines:
            cond_match = condition_line_re.match(line)
            if cond_match:
                # Found a new condition. Save the previous one if it exists.
                if current_condition is not None:
                    # Join the collected lines for the previous prompt.
                    prompt_text = "\n".join(current_prompt_lines).strip()
                    if prompt_text: # Only add if there was content
                        parsed_conditions.append((current_condition, prompt_text))

                # Start the new condition block
                current_condition = cond_match.group(1).strip()
                # The rest of the line is the start of the new prompt.
                # lstrip() handles space between ']' and the text.
                current_prompt_lines = [cond_match.group(2).lstrip()]
            elif current_condition is not None:
                # This line is a continuation of the prompt for the current condition.
                current_prompt_lines.append(line)

        # After the loop, save the last collected condition block
        if current_condition is not None:
            prompt_text = "\n".join(current_prompt_lines).strip()
            if prompt_text:
                parsed_conditions.append((current_condition, prompt_text))

        # Evaluate the collected conditions and build the final result
        result_parts = []
        for condition_str, prompt_text in parsed_conditions:
            if self._evaluate_condition(condition_str):
                result_parts.append(prompt_text)

        return "\n".join(result_parts)

    def _evaluate_condition(self, condition_str):
        """
        Evaluates a condition string without using eval().

        Supports:
        - Simple boolean variables: "tts_on"
        - Negated variables: "not tts_on"
        - 'contains' checks: "message.contains(\"text\")"
        - Logical operators: "and", "or"
        """
        # Normalize whitespace for easier parsing
        condition_str = condition_str.strip()

        # Handle logical operators by splitting and evaluating recursively
        # This handles complex conditions by breaking them down.
        if " or " in condition_str:
            parts = condition_str.split(" or ", 1)
            return self._evaluate_condition(parts[0]) or self._evaluate_condition(parts[1])
        
        if " and " in condition_str:
            parts = condition_str.split(" and ", 1)
            return self._evaluate_condition(parts[0]) and self._evaluate_condition(parts[1])

        # Handle "not" operator
        is_negated = False
        if condition_str.startswith("not "):
            is_negated = True
            condition_str = condition_str[4:].strip()
            
        # Handle "contains" method
        contains_match = re.match(r'(\w+)\.contains\((.*?)\)', condition_str)
        if contains_match:
            var_name = contains_match.group(1)
            value_to_check = contains_match.group(2).strip('"\'') # Remove quotes
            
            variable_content = str(self.get_variable(var_name) or "")
            result = value_to_check in variable_content
        else:
            # Handle simple boolean variable
            result = bool(self.get_variable(condition_str))

        return not result if is_negated else result
