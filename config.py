from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postmark_server_token: str
    brevo_api_key: str
    anthropic_api_key: str
    github_token: str = ""
    pass_threshold: int = 65
    test_email_override: str = ""  # If set, all outbound emails go here instead of actual candidate

    # Rubric weights (must sum to 100)
    weight_shipped_products: int = 30
    weight_technical_depth: int = 25
    weight_business_thinking: int = 20
    weight_speed_of_execution: int = 15
    weight_communication_clarity: int = 10

    class Config:
        env_file = ".env"
        env_ignore_empty = True


settings = Settings()
