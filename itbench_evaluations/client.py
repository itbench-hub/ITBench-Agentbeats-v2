import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

def create_judge_client() -> OpenAI:
    """Create a client that uses OpenAI with EVALUATOR_* environment variables.
    
    Returns:
        OpenAI client configured with environment variables
    """
    return OpenAI(
        base_url=os.environ.get("EVALUATOR_URL"),
        api_key=os.environ.get("EVALUATOR_API_KEY"),
    )


def get_judge_model() -> str:
    """Get judge model name from environment variables.
    
    Prioritizes EVALUATOR_PROVIDER/EVALUATOR_MODEL, then falls back to PROVIDER/MODEL.
    
    Returns:
        Model name string constructed as 'provider/model'
    """
    # Check EVALUATOR_* first (matching previous LiteLLM backend logic)
    provider = os.environ.get("EVALUATOR_PROVIDER")
    model = os.environ.get("EVALUATOR_MODEL")
    
    if not provider or not model:
         # Fallback to PROVIDER/MODEL
         provider = os.environ.get("PROVIDER")
         model = os.environ.get("MODEL")
    
    if provider:
        return f"{provider}/{model}"
    return model


