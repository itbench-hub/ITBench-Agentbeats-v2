"""OpenAI-compatible client for judge LLM using LiteLLM."""

import os
from litellm import completion
from dotenv import load_dotenv

load_dotenv()

class LiteLLMBackend:
    def create(self, **kwargs):
        """Execute completion using LiteLLM with environment variables."""
        provider = os.environ.get("PROVIDER")
        model = os.environ.get("MODEL")
        base_url = os.environ.get("URL")
        api_key = os.environ.get("API_KEY")
        
        # Override parameters with environment variables logic from green_agent
        if provider and model:
            kwargs['model'] = f"{provider}/{model}"
            
        if base_url:
            kwargs['base_url'] = base_url
            
        if api_key:
            kwargs['api_key'] = api_key
            
        # Add internal retries if not specified (green_agent uses 5)
        # But allow kwargs to override
        if 'num_retries' not in kwargs:
             kwargs['num_retries'] = 5

        return completion(**kwargs)

class LiteLLMClient:
    """Wrapper to mimic OpenAI client structure but use LiteLLM."""
    def __init__(self):
        self.chat = self
        self.completions = LiteLLMBackend()

def create_judge_client() -> LiteLLMClient:
    """Create a client that uses LiteLLM with GREEN_* environment variables.
    
    Returns:
        LiteLLMClient mimicking OpenAI client structure
    """
    return LiteLLMClient()


def get_judge_model() -> str:
    """Get judge model name from GREEN_* environment variables.
    
    Returns:
        Model name string constructed as 'provider/model'
    """
    provider = os.environ.get("PROVIDER")
    model = os.environ.get("MODEL")
    
    if provider:
        return f"{provider}/{model}"
    return model


