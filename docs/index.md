# WorldFork Documentation

WorldFork is agent-operated social simulation infrastructure for branching timelines. It is best understood as a Monte Carlo tree search of the real world: initialize a scenario, explore plausible branches, score terminal outcomes, and compare the resulting timelines. It exposes a FastAPI backend, Celery workers, durable Postgres and Redis state, an artifact store, and the `worldfork` CLI for agent and operator workflows.

## Wiki And Documentation Mirrors

| Resource | Use this when |
| --- | --- |
| [Read the Docs](https://worldfork.readthedocs.io/en/latest/) | You want the maintained documentation site generated from this repo. |
| [Deep Wiki](https://deepwiki.com/Hilo-Hilo/WorldFork) | You want a DeepWiki view of the repository and codebase. |

The documentation is organized around the jobs people actually need to do:

| Start here | Use this when |
| --- | --- |
| [Setup](setup.md) | You need a local backend, CLI, and agent skill installed |
| [CLI](cli.md) | You need command examples or agent-safe operating patterns |
| [Demos](demos.md) | You want to run Atlas or the live runtime smoke |
| [Architecture](architecture.md) | You need the runtime model and storage layers |
| [Reporting](reporting.md) | You need report versioning, artifacts, and continuations |
| [Agent Interface](agent.md) | You are writing or operating an AI agent against WorldFork |

```{toctree}
:maxdepth: 2
:caption: Guides

setup
cli
demos
architecture
reporting
agent
testing
release
```
