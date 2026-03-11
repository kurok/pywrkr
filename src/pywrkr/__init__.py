"""pywrkr - A Python HTTP benchmarking tool inspired by wrk and Apache ab."""

from pywrkr.config import (  # noqa: F401
    RequestResult,
    LatencyBreakdown,
    WorkerStats,
    BenchmarkConfig,
    Threshold,
    AutofindConfig,
    StepResult,
    ScenarioStep,
    Scenario,
    load_scenario,
)

from pywrkr.traffic_profiles import (  # noqa: F401
    TrafficProfile,
    SineProfile,
    StepProfile,
    SawtoothProfile,
    SquareProfile,
    SpikeProfile,
    BusinessHoursProfile,
    CsvProfile,
    parse_traffic_profile,
    RateLimiter,
)

from pywrkr.reporting import (  # noqa: F401
    RICH_AVAILABLE,
    OTEL_AVAILABLE,
    format_bytes,
    format_duration,
    print_latency_histogram,
    compute_percentiles,
    parse_threshold,
    evaluate_thresholds,
    _get_metric_value,
    _compare,
    print_threshold_results,
    print_percentiles,
    print_rps_timeline,
    build_results_dict,
    write_csv_output,
    write_json_output,
    generate_html_report,
    generate_gatling_html_report,
    _html_escape,
    write_html_report,
    export_to_otel,
    export_to_prometheus,
    print_results,
    _format_latency_short,
    print_autofind_summary,
    print_multi_url_summary,
    build_multi_url_json,
)

from pywrkr.workers import (  # noqa: F401
    LiveDashboard,
    make_url,
    create_trace_config,
    aggregate_breakdowns,
    worker,
    user_worker,
    scenario_worker,
    show_progress,
    run_benchmark,
    run_user_simulation,
    _step_passed,
    _extract_step_result,
    run_autofind,
    _write_autofind_json,
)

from pywrkr.distributed import (  # noqa: F401
    _serialize_config,
    _deserialize_config,
    _serialize_stats,
    _deserialize_stats,
    _send_msg,
    _recv_msg,
    merge_worker_stats,
    run_master,
    run_worker_node,
)

from pywrkr.multi_url import (  # noqa: F401
    UrlEntry,
    MultiUrlResult,
    load_url_file,
    run_multi_url,
)

from pywrkr.main import parse_header  # noqa: F401

# Re-export main function under a different name to avoid shadowing pywrkr.main module
from pywrkr.main import main as cli_main  # noqa: F401

__version__ = "1.0.2"
