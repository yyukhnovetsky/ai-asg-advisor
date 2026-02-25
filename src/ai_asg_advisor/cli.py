"""CLI entry point for AI ASG Advisor."""

from __future__ import annotations

import logging
import sys

import click

from ai_asg_advisor import __version__
from ai_asg_advisor.config import load_config

VALID_PERIODS = ("1w", "2w", "3w", "1m", "2m")
VALID_STRATEGIES = ("cost", "balanced", "performance")
VALID_FORMATS = ("terraform", "json", "text")


@click.command()
@click.option("--asg", required=True, help="Auto Scaling Group name (e.g., celery-email-sync-py39).")
@click.option("--queue", required=True, help="RabbitMQ queue name (e.g., email_sync_v2).")
@click.option(
    "--period",
    type=click.Choice(VALID_PERIODS, case_sensitive=False),
    default="2w",
    show_default=True,
    help="Analysis time period.",
)
@click.option(
    "--strategy",
    type=click.Choice(VALID_STRATEGIES, case_sensitive=False),
    default="balanced",
    show_default=True,
    help="Optimization strategy.",
)
@click.option("--profile", default=None, help="AWS profile name (default: ai-adviser).")
@click.option("--role", default=None, help="IAM role ARN or name to assume (for EC2 execution).")
@click.option("--region", default=None, help="AWS region (default: from config or us-east-1).")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(VALID_FORMATS, case_sensitive=False),
    default="terraform",
    show_default=True,
    help="Output format.",
)
@click.option("--output", "output_file", default=None, type=click.Path(), help="Write output to file instead of stdout.")
@click.option("--config", "config_path", default=None, type=click.Path(exists=True), help="Path to config file.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable verbose logging.")
@click.version_option(version=__version__, prog_name="ai-asg-advisor")
def main(
    asg: str,
    queue: str,
    period: str,
    strategy: str,
    profile: str | None,
    role: str | None,
    region: str | None,
    output_format: str,
    output_file: str | None,
    config_path: str | None,
    verbose: bool,
) -> None:
    """Analyze ASG scaling behavior and generate optimized step policies using LLM analysis."""
    from pathlib import Path

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    config = load_config(Path(config_path) if config_path else None)

    # CLI args override config
    if profile:
        config.aws.profile = profile
    elif not config.aws.profile and not role:
        config.aws.profile = "ai-adviser"
    if role:
        config.aws.role_arn = role
    if region:
        config.aws.region = region

    if not config.llm.api_key:
        raise click.ClickException(
            "Claude API key required. Set ANTHROPIC_API_KEY env var or add it to config file."
        )

    click.echo(f"AI ASG Advisor v{__version__}", err=True)
    click.echo(f"  ASG:      {asg}", err=True)
    click.echo(f"  Queue:    {queue}", err=True)
    click.echo(f"  Period:   {period}", err=True)
    click.echo(f"  Strategy: {strategy}", err=True)
    click.echo(f"  Region:   {config.aws.region}", err=True)
    click.echo(f"  Format:   {output_format}", err=True)
    click.echo(err=True)

    # -- Pipeline --
    from ai_asg_advisor.aws_auth import create_session, get_client, get_pricing_client, verify_credentials
    from ai_asg_advisor.cloudwatch_collector import get_queue_depth_metrics, get_asg_capacity_metrics
    from ai_asg_advisor.asg_collector import get_asg_config, get_scaling_policies
    from ai_asg_advisor.instance_collector import get_instance_recommendations
    from ai_asg_advisor.preprocessor import build_analysis_context
    from ai_asg_advisor.llm_advisor import call_llm_advisor
    from ai_asg_advisor.output_formatter import format_output

    # Step 1: Authenticate
    click.echo("[1/6] Authenticating with AWS...", err=True)
    session = create_session(config.aws)
    verify_credentials(session)

    cw_client = get_client(session, "cloudwatch")
    asg_client = get_client(session, "autoscaling")
    ec2_client = get_client(session, "ec2")
    pricing_client = get_pricing_client(session)

    # Step 2: Collect metrics
    click.echo("[2/6] Collecting CloudWatch metrics...", err=True)
    queue_metrics = get_queue_depth_metrics(cw_client, queue, period)
    capacity_metrics = get_asg_capacity_metrics(cw_client, asg, period)

    if not queue_metrics.values:
        raise click.ClickException(
            f"No queue depth data found for queue '{queue}' in the last {period}. "
            "Check the queue name and CloudWatch namespace."
        )

    # Step 3: Collect ASG configuration
    click.echo("[3/6] Collecting ASG configuration...", err=True)
    asg_config = get_asg_config(asg_client, ec2_client, asg)
    policies = get_scaling_policies(asg_client, asg)

    # Step 4: Collect instance pricing
    click.echo("[4/6] Collecting instance type pricing...", err=True)
    primary_instance_type = asg_config.instance_types[0] if asg_config.instance_types else None
    if not primary_instance_type:
        raise click.ClickException(
            f"Could not determine instance type for ASG '{asg}'. "
            "Check the launch template or launch configuration."
        )

    instance_recs = get_instance_recommendations(
        ec2_client, pricing_client, primary_instance_type, config.aws.region,
    )

    # Step 5: Pre-process and call LLM
    click.echo("[5/6] Analyzing with Claude...", err=True)
    analysis_context = build_analysis_context(
        queue, queue_metrics, asg_config, capacity_metrics, policies,
    )
    llm_response = call_llm_advisor(
        config.llm, analysis_context, instance_recs, strategy, period,
    )

    # Step 6: Format output
    click.echo("[6/6] Generating output...", err=True)
    output = format_output(llm_response, asg, output_format)

    if output_file:
        Path(output_file).write_text(output)
        click.echo(f"Output written to {output_file}", err=True)
    else:
        click.echo(output)

    click.echo("Done.", err=True)


if __name__ == "__main__":
    main()
