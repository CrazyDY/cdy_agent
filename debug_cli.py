import os

from cdy_agent.cli import app

if __name__ == '__main__':
    # WARNING: Do NOT hardcode API keys or secrets into repository files.
    # Please set required credentials in your environment before running.
    # Example (bash):
    #   export OPENAI_API_KEY="your-api-key"
    #   export OPENAI_BASE_URL="https://api.openai.com/v1"

    # Ensure we don't leak any secrets by overriding with environment defaults.
    os.environ.pop('OPENAI_API_KEY', None)
    os.environ['OPENAI_BASE_URL'] = os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1')
    os.environ['CDY_AGENT_MODEL'] = os.environ.get('CDY_AGENT_MODEL', 'deepseek-v4-flash')

    app()
