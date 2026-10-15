"""
Dashboard API Key
"""
class DashboardAPIKey:
    def __init__(self, Key: str, CreatedAt: str, ExpiredAt: str, AllowedConfigurations: str = "*", Scope: str = "admin", Label: str = ""):
        self.Key = Key
        self.CreatedAt = CreatedAt
        self.ExpiredAt = ExpiredAt
        self.AllowedConfigurations = AllowedConfigurations or "*"
        self.Scope = Scope or "admin"
        self.Label = Label or ""

    def is_configuration_allowed(self, config_name: str) -> bool:
        if not config_name:
            return True
        if not self.AllowedConfigurations or self.AllowedConfigurations.strip() == "*":
            return True
        allowed = [c.strip() for c in self.AllowedConfigurations.split(",") if c.strip()]
        return config_name in allowed

    def is_scope_allowed(self, required_scope: str) -> bool:
        hierarchy = {"read": 1, "peers": 2, "admin": 3}
        current_level = hierarchy.get(self.Scope, 1)
        required_level = hierarchy.get(required_scope, 1)
        return current_level >= required_level

    def toJson(self):
        return self.__dict__