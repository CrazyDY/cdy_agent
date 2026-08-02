import os

from cdy_agent.cli import app

if __name__ == '__main__':
    os.environ['OPENAI_API_KEY'] = 'sk-23000b6fba2e41c5b05be2eb4cc021a1'
    os.environ['OPENAI_BASE_URL'] = 'https://api.deepseek.com'
    # os.environ['OPENAI_BASE_URL'] = 'http://127.0.0.1:11434/v1'
    os.environ['CDY_AGENT_MODEL'] = 'deepseek-v4-flash'
    # os.environ['CDY_AGENT_API_MODE'] = 'chat_completions'
    # os.environ['CDY_AGENT_LOG_LEVEL'] = 'DEBUG'
    # os.environ['CDY_AGENT_INPUT_COST_PER_MILLION'] = '0.02'
    # os.environ['CDY_AGENT_OUTPUT_COST_PER_MILLION'] = '2'
    app()