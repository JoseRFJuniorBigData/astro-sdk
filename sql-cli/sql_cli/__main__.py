from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from dotenv import load_dotenv
from rich import print as rprint
from typer import Exit

import sql_cli
from sql_cli.astro.command import AstroCommand
from sql_cli.astro.group import AstroGroup
from sql_cli.constants import DEFAULT_AIRFLOW_HOME, DEFAULT_DAGS_FOLDER
from sql_cli.exceptions import ConnectionFailed, DagCycle, EmptyDag, SqlFilesDirectoryNotFound

if TYPE_CHECKING:
    from sql_cli.project import Project

load_dotenv()
app = typer.Typer(
    name="flow",
    cls=AstroGroup,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    rich_markup_mode="rich",
)

airflow_logger = logging.getLogger("airflow")
airflow_logger.setLevel(logging.CRITICAL)
airflow_logger.propagate = False


@app.command(
    cls=AstroCommand,
    help="Print the SQL CLI version.",
)
def version() -> None:
    rprint("Astro SQL CLI", sql_cli.__version__)


@app.command(
    cls=AstroCommand,
    help="Print additional information about the project.",
)
def about() -> None:
    rprint("Find out more: https://docs.astronomer.io/astro/cli/sql-cli")


@app.command(
    cls=AstroCommand,
    help="Generate the Airflow DAG from a directory of SQL files.",
)
def generate(
    workflow_name: str = typer.Argument(
        default=...,
        show_default=False,
        help="name of the workflow directory within workflows directory.",
    ),
    env: str = typer.Option(
        default="default",
        help="environment to run in",
    ),
    project_dir: Path = typer.Option(
        None, dir_okay=True, metavar="PATH", help="(Optional) Default: current directory.", show_default=False
    ),
    generate_tasks: bool = typer.Option(
        default=False,
        help="whether to explicitly generate the tasks in your SQL CLI "
        "DAG. By default we will keep the DAGs smaller and read SQL "
        "files at runtime",
        show_default=True,
    ),
) -> None:
    from sql_cli.project import Project
    from sql_cli.utils.airflow import retrieve_airflow_database_conn_from_config, set_airflow_database_conn

    project_dir_absolute = project_dir.resolve() if project_dir else Path.cwd()
    project = Project(project_dir_absolute)
    project.load_config(env)

    # Since we are using the Airflow ORM to interact with connections, we need to tell Airflow to use our airflow.db
    # The usual route is to set $AIRFLOW_HOME before Airflow is imported. However, in the context of the SQL CLI, we
    # decide this during runtime, depending on the project path and SQL CLI configuration.
    airflow_meta_conn = retrieve_airflow_database_conn_from_config(project.directory / project.airflow_home)
    set_airflow_database_conn(airflow_meta_conn)

    rprint(
        f"\nGenerating the DAG file from workflow [bold blue]{workflow_name}[/bold blue]"
        f" for [bold]{env}[/bold] environment..\n"
    )
    dag_file = _generate_dag(project=project, workflow_name=workflow_name, generate_tasks=generate_tasks)
    rprint("The DAG file", dag_file.resolve(), "has been successfully generated. 🎉")


@app.command(
    cls=AstroCommand,
    help="""
    Validate Airflow connection(s) provided in the configuration file for the given environment.
    """,
)
def validate(
    project_dir: Path = typer.Argument(
        None, dir_okay=True, metavar="PATH", help="(Optional) Default: current directory.", show_default=False
    ),
    env: str = typer.Option(
        default="default",
        help="(Optional) Environment used to declare the connections to be validated",
    ),
    connection: str = typer.Option(
        default=None,
        help="(Optional) Identifier of the connection to be validated. By default checks all the env connections.",
    ),
) -> None:
    from sql_cli.connections import validate_connections
    from sql_cli.project import Project
    from sql_cli.utils.airflow import retrieve_airflow_database_conn_from_config, set_airflow_database_conn

    project_dir_absolute = project_dir.resolve() if project_dir else Path.cwd()
    project = Project(project_dir_absolute)
    project.load_config(environment=env)

    # Since we are using the Airflow ORM to interact with connections, we need to tell Airflow to use our airflow.db
    # The usual route is to set $AIRFLOW_HOME before Airflow is imported. However, in the context of the SQL CLI, we
    # decide this during runtime, depending on the project path and SQL CLI configuration.
    airflow_meta_conn = retrieve_airflow_database_conn_from_config(project.directory / project.airflow_home)
    set_airflow_database_conn(airflow_meta_conn)

    rprint(f"Validating connection(s) for environment '{env}'")
    validate_connections(connections=project.connections, connection_id=connection)


@app.command(
    cls=AstroCommand,
    help="""
    Run a workflow locally. This task assumes that there is a local airflow DB (can be a SQLite file), that has been
    initialized with Airflow tables.
    """,
)
def run(
    workflow_name: str = typer.Argument(
        default=...,
        show_default=False,
        help="name of the workflow directory within workflows directory.",
    ),
    env: str = typer.Option(
        metavar="environment",
        default="default",
        help="environment to run in",
    ),
    project_dir: Path = typer.Option(
        None, dir_okay=True, metavar="PATH", help="(Optional) Default: current directory.", show_default=False
    ),
    generate_tasks: bool = typer.Option(
        default=False,
        help="whether to explicitly generate the tasks in your SQL CLI "
        "DAG. By default we will keep the DAGs smaller and read SQL "
        "files at runtime",
        show_default=True,
    ),
    verbose: bool = typer.Option(False, help="Whether to show airflow logs", show_default=True),
) -> None:
    from airflow.utils.state import State

    from sql_cli import run_dag as dag_runner
    from sql_cli.project import Project
    from sql_cli.utils.airflow import (
        get_dag,
        retrieve_airflow_database_conn_from_config,
        set_airflow_database_conn,
    )

    project_dir_absolute = project_dir.resolve() if project_dir else Path.cwd()
    project = Project(project_dir_absolute)
    project.update_config(environment=env)
    project.load_config(env)

    # Since we are using the Airflow ORM to interact with connections, we need to tell Airflow to use our airflow.db
    # The usual route is to set $AIRFLOW_HOME before Airflow is imported. However, in the context of the SQL CLI, we
    # decide this during runtime, depending on the project path and SQL CLI configuration.
    airflow_meta_conn = retrieve_airflow_database_conn_from_config(project.directory / project.airflow_home)
    set_airflow_database_conn(airflow_meta_conn)

    dag_file = _generate_dag(project=project, workflow_name=workflow_name, generate_tasks=generate_tasks)
    dag = get_dag(dag_id=workflow_name, subdir=dag_file.parent.as_posix(), include_examples=False)

    rprint(f"\nRunning the workflow [bold blue]{dag.dag_id}[/bold blue] for [bold]{env}[/bold] environment\n")
    try:
        dr = dag_runner.run_dag(
            dag,
            run_conf=project.airflow_config,
            connections={c.conn_id: c for c in project.connections},
            verbose=verbose,
        )
    except ConnectionFailed as connection_failed:
        rprint(
            f"  [bold red]{connection_failed}[/bold red] using connection [bold]{connection_failed.conn_id}[/bold]"
        )
        raise Exit(code=1)
    except Exception as exception:
        rprint(f"  [bold red]{exception}[/bold red]")
        raise Exit(code=1)
    final_state = dr.state
    if dr.state == State.SUCCESS:
        final_state = "[bold green]SUCCESS 🚀[/bold green]"
    elif dr.state == State.FAILED:
        final_state = "[bold red]FAILED 💥[/bold red]"
    rprint(f"Completed running the workflow {dr.dag_id}. Final state: {final_state}")
    elapsed_seconds = (dr.end_date - dr.start_date).microseconds / 10**6
    rprint(f"Total elapsed time: [bold blue]{elapsed_seconds:.2}s[/bold blue]")


@app.command(
    cls=AstroCommand,
    help="""
    Initialise a project structure to write workflows using SQL files.

    Examples of usage:
    $ flow init
    $ flow init .
    $ flow init project_name

    By default, the project structure includes:
    ├── config: withholds configuration, e.g. database connections, within each environment directory
    ├── data: directory which contains datasets, including SQLite databases used by the examples
    └── workflows: directory where SQL workflows are declared, by default has two examples of workflow

    Next steps:
    * Update the file `config/default/configuration.yaml` to declare database connections.
    * Create SQL workflows within the `workflows` folder.
    """,
)
def init(
    project_dir: Path = typer.Argument(
        None, dir_okay=True, metavar="PATH", help="(Optional) Default: current directory.", show_default=False
    ),
    airflow_home: Path = typer.Option(
        None,
        dir_okay=True,
        help=f"(Optional) Set the Airflow Home. Default: {DEFAULT_AIRFLOW_HOME}",
        show_default=False,
    ),
    airflow_dags_folder: Path = typer.Option(
        None,
        dir_okay=True,
        help=f"(Optional) Set the DAGs Folder. Default: {DEFAULT_DAGS_FOLDER}",
        show_default=False,
    ),
) -> None:
    from sql_cli.project import Project

    project_dir_absolute = project_dir.resolve() if project_dir else Path.cwd()
    project = Project(project_dir_absolute, airflow_home, airflow_dags_folder)
    project.initialise()
    rprint("Initialized an Astro SQL project at", project.directory)


def _generate_dag(project: Project, workflow_name: str, generate_tasks: bool) -> Path:
    """
    Helper function for generating DAGs with proper exceptions. Moved here since this function is used by
    multiple commands

    :param project: project object containing project metadata
    :param workflow_name: name of the workflow directory for this DAG
    :param generate_tasks: whether to render each task individually or use the render function
    :return: the path to the generated dag_file
    """
    from sql_cli import dag_generator
    from sql_cli.utils.airflow import check_for_dag_import_errors

    try:
        dag_file = dag_generator.generate_dag(
            directory=project.directory / project.workflows_directory / workflow_name,
            dags_directory=project.airflow_dags_folder,
            generate_tasks=generate_tasks,
        )
    except EmptyDag:
        rprint(f"[bold red]The workflow {workflow_name} does not have any SQL files![/bold red]")
        raise Exit(code=1)
    except SqlFilesDirectoryNotFound:
        rprint(f"[bold red]The workflow {workflow_name} does not exist![/bold red]")
        raise Exit(code=1)
    except DagCycle as dag_cycle:
        rprint(f"[bold red]The workflow {workflow_name} contains a cycle! {dag_cycle}[/bold red]")
        raise Exit(code=1)
    import_errors = check_for_dag_import_errors(dag_file)
    if import_errors:
        all_errors = "\n\n".join(list(import_errors.values()))
        rprint(f"[bold red]Workflow failed to render[/bold red]\n errors found:\n\n {all_errors}")
        raise Exit(code=1)
    return dag_file


if __name__ == "__main__":  # pragma: no cover
    app()
