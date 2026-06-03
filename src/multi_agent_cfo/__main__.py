"""Entry point for `python -m multi_agent_cfo`.

Runs the scheduler with default configuration: reads clients from
clients/clients.yaml and uses the ConsoleAdapter for confirmation.
"""

from multi_agent_cfo.scheduler.scheduler import run_scheduler


if __name__ == "__main__":
    run_scheduler()