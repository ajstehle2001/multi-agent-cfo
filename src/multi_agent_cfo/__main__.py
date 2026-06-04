"""Entry point for `python -m multi_agent_cfo`.

Runs the scheduler with default configuration: reads clients from
clients/clients.yaml, uses the ConsoleAdapter for confirmation, and
uses the FileOutputAdapter so approved memos are written to disk
under output/memos/ for easy review.
"""

from multi_agent_cfo.output.output import FileOutputAdapter
from multi_agent_cfo.scheduler.scheduler import run_scheduler


if __name__ == "__main__":
    run_scheduler(output=FileOutputAdapter())