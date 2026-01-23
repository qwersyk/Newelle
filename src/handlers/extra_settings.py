from collections.abc import Callable


class ExtraSettings:
    @staticmethod 
    def Setting(key: str, title: str, description: str, default: object, folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        """
        Create a new setting

        Args:
            key: key of the setting, used to be retrived 
            title: title of the setting, shown to the user 
            description: description of the setting, shown to the user 
            default: default value of the setting 
            folder: if not None, near the setting it will be shown a button to open the specified folder 
            website: if not None, near the setting it will be shown a button to open the specified website 
            update_settings: if True, when the setting is changed, the settings will be automatically updated 
            refresh: if not None, near the setting it will be shown a button to refresh the specified function. When clicked the function is executed 
            refresh_icon: if not None, the icon of the refresh button 

        Returns:
            dict: the setting in this format 
        """
        r = {
            "key": key,
            "title": title,
            "description": description,
            "default": default,
            "update_settings": update_settings
        }
        if website is not None:
            r["website"] = website
        if folder is not None:
            r["folder"] = folder
        if refresh is not None:
            r["refresh"] = refresh
        if refresh_icon is not None:
            r["refresh_icon"] = refresh_icon
        return r
    
    @staticmethod
    def EntrySetting(key:str, title: str, description: str, default: str, 
                     folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None, password: bool = False) -> dict:
        """
        Create a new entry setting, which can be used to enter a string

        Args:
            key: key of the setting 
            title: title of the setting  
            description: description of the setting 
            default: default value of the setting 
            folder: if not None, near the setting it will be shown a button to open the specified folder 
            website: if not None, near the setting it will be shown a button to open the specified website 
            update_settings: if True, when the setting is changed, the settings will be automatically updated 
            refresh: if not None, near the setting it will be shown a button to refresh the specified function. When clicked the function is executed 
            refresh_icon: if not None, the icon of the refresh button
            password: if True, the entry will be shown as a password

        Returns:
            dict: the setting in this format 
        """
        r = ExtraSettings.Setting(key, title, description, default, folder, website, update_settings, refresh, refresh_icon)
        r["type"] = "entry"
        r["password"] = password
        return r

    @staticmethod
    def MultilineEntrySetting(key:str, title: str, description: str, default: str, 
                     folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        """
        Create a new entry setting, which can be used to enter a string with multiple line (shown as an expander row)

        Args:
            key: key of the setting 
            title: title of the setting 
            description: description of the setting 
            default: default value of the setting 
            folder: if not None, near the setting it will be shown a button to open the specified folder 
            website: if not None, near the setting it will be shown a button to open the specified website 
            update_settings: if True, when the setting is changed, the settings will be automatically updated 
            refresh: if not None, near the setting it will be shown a button to refresh the specified function. When clicked the function is executed 
            refresh_icon: if not None, the icon of the refresh button 

        Returns:
            dict: the setting in this format 
        """
        r = ExtraSettings.Setting(key, title, description, default, folder, website, update_settings, refresh, refresh_icon)
        r["type"] = "multilineentry"
        return r
    
    @staticmethod
    def ToggleSetting(key:str, title: str, description: str, default: bool, 
                      folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        """
        Create a new toggle setting. This setting can be used to enable or disable a setting

        Args:
            key: key of the setting 
            title: title of the setting 
            description: description of the setting 
            default: default value of the setting 
            folder: if not None, near the setting it will be shown a button to open the specified folder 
            website: if not None, near the setting it will be shown a button to open the specified website 
            update_settings: if True, when the setting is changed, the settings will be automatically updated 
            refresh: if not None, near the setting it will be shown a button to refresh the specified function. When clicked the function is executed 
            refresh_icon: if not None, the icon of the refresh button 

        Returns:
            dict: the setting in this format 
        """
        r = ExtraSettings.Setting(key, title, description, default, folder, website, update_settings, refresh, refresh_icon)
        r["type"] = "toggle"
        return r

    @staticmethod 
    def NestedSetting(key:str, title: str, description: str, extra_settings: list, 
                      folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        """
        Create a new nested setting, which can be used to wrap other settings.

        Args:
            key: key of the setting  
            title: title of the setting 
            description: description of the setting 
            extra_settings: list of extra settings 
            folder: if not None, near the setting it will be shown a button to open the specified folder 
            website: if not None, near the setting it will be shown a button to open the specified website 
            update_settings: if True, when the setting is changed, the settings will be automatically updated 
            refresh: if not None, near the setting it will be shown a button to refresh the specified function. When clicked the function is executed 
            refresh_icon: if not None, the icon of the refresh button 

        Returns:
            dict: the setting in this format 
        """
        r = ExtraSettings.Setting(key, title, description, None, folder, website, update_settings, refresh, refresh_icon)
        r["type"] = "nested"
        r["extra_settings"] = extra_settings
        return r

    @staticmethod 
    def DownloadSetting(key:str, title: str, description: str, is_installed: bool, callback: Callable, download_percentage: Callable, download_icon: str|None = None, 
                        folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        """
        Create a new download setting. This will show a row with something that is downloadable. 
        When clicked, it will start the download and show a progressbar.
        If installed, a delete button will be shown.

        Args:
            key: key of the setting 
            title: title of the setting               
            description: description of the setting             
            is_installed: if True, the delete button will be shown                         
            callback: the function that will be executed when the download or delete button is clicked. Must download the file ON THE SAME THREAD                                            
            download_percentage: the function that will be executed to get the download percentage (float between 0.0 and 1.0)                                                           
            download_icon: if not None, the icon of the download button                                                                       
            folder: if not None, near the setting it will be shown a button to open the specified folder
            website: if not None, near the setting it will be shown a button to open the specified website
            update_settings: if True, when the setting is changed, the settings will be automatically updated 
            refresh: if not None, near the setting it will be shown a button to refresh the specified function. When clicked the function is executed                                                                                       
            refresh_icon: if not None, the icon of the refresh button                                                                                                                          

        Returns:
            
        """
        r = ExtraSettings.Setting(key, title, description, is_installed, folder, website, update_settings, refresh, refresh_icon)
        r["type"] = "download"
        r["callback"] = callback
        r["download_percentage"] = download_percentage
        if download_icon is not None:
            r["download-icon"] = download_icon
        r["is_installed"] = is_installed
        return r

    @staticmethod
    def ButtonSetting(key:str, title: str, description: str, callback: Callable, label: str|None = None, icon: str|None = None,
                      folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        """
        Create a new button setting. 
        This setting shows a row with button and does not actually get a value.

        Args:
            key: key of the setting 
            title: title of the setting 
            description: description of the setting
            callback: the function that will be executed when the button is clicked                            
            label: if not None, the label of the button                                                               
            icon: if not None, the icon of the button                                    
            folder: if not None, near the setting it will be shown a button to open the specified folder
            website: if not None, near the setting it will be shown a button to open the specified website
            update_settings: if True, when the setting is changed, the settings will be automatically updated                                                                            
            refresh: if not None, near the setting it will be shown a button to refresh the specified function. When clicked the function is executed                                                                                       
            refresh_icon: if not None, the icon of the refresh button                                                                                                                          

        Returns:
            dict: the setting in this format 
        """
        r = ExtraSettings.Setting(key, title, description, None, folder, website, update_settings, refresh, refresh_icon)
        r["type"] = "button"
        r["callback"] = callback
        if label is not None:
            r["label"] = label
        if icon is not None:
            r["icon"] = icon
        return r

    @staticmethod
    def ComboSetting(key: str, title: str, description: str, values: list | dict | tuple, default: str,
                     folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        """
        Create a new combo setting. Shows a list of values to pick from

        Args:
            key: key of the setting                 
            title: title of the setting               
            description: description of the setting             
            values: the values of the setting                              
            default: the default value of the setting                       
            folder: if not None, near the setting it will be shown a button to open the specified folder                                
            website: if not None, near the setting it will be shown a button to open the specified website                                                                                  
            update_settings: if True, when the setting is changed, the settings will be automatically updated                                                                            
            refresh: if not None, near the setting it will be shown a button to refresh the specified function. When clicked the function is executed                                                                                       
            refresh_icon: if not None, the icon of the refresh button

        Returns:
            
        """
        r = ExtraSettings.Setting(key, title, description, default, folder, website, update_settings, refresh, refresh_icon)
        r["type"] = "combo"
        values = ExtraSettings.fix_models_format(values)
        if type(values) is list:
            val = tuple()
            for v in values:
                val += ((v,v), )
        elif type(values) is dict:
            val = tuple()
            for k, v in values.items():
                val += ((k, v), )
        else:
            val = values
        r["values"] = val
        return r
    
    @staticmethod
    def fix_models_format(models):
        if type(models) is not list or len(models) == 0 or type(models[0]) is not list:
            return models
        m = tuple()
        for model in models:
            m += ((model[0], model[1]),)
        return m

    @staticmethod 
    def ScaleSetting(key: str, title: str, description: str, default: float, min: float, max: float, round: int,
                     folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        """
        Create a new scale setting. Used for numeric values, shows a slider.

        Args:
            key: key of the setting 
            title: title of the setting 
            description: description of the setting             
            default: the default value of the setting                             
            min: the minimum value of the setting                                   
            max: the maximum value of the setting                               
            round: the number of digits to round to                             
            folder: if not None, near the setting it will be shown a button to open the specified folder                               
            website: if not None, near the setting it will be shown a button to open the specified website                                                                                  
            update_settings: if True, when the setting is changed, the settings will be automatically updated                                                                            
            refresh: if not None, near the setting it will be shown a button to refresh the specified function. When clicked the function is executed                                                                                       
            refresh_icon: if not None, the icon of the refresh button                                                                                                                          

        Returns:
             - dict: the setting in this format
        """
        r = ExtraSettings.Setting(key, title, description, default, folder, website, update_settings, refresh, refresh_icon)
        r["type"] = "range"
        r["min"] = min
        r["max"] = max
        r["round-digits"] = round
        return r
