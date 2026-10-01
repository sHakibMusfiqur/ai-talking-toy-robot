class ProviderError(Exception):
   

    def __init__(self, component: str = "", message: str = ""):
        self.component = component
        detail = f"{component}: {message}" if component and message else component or message
        super().__init__(detail or "provider failure")
