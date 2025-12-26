from gis_data.services import routing_service


def get_preference_coefficients(preference):
    """Get alpha and beta coefficients for preference."""
    coefficients = {
        "fast": (1.0, 0.1),
        "balanced": (0.6, 0.4),
        "most_winding": (0.3, 0.7),
    }
    return coefficients.get(preference, (0.6, 0.4))


def calculate_scenic_cost_sql(alpha, beta):
    """Generate SQL for scenic cost calculation."""
    return f"({alpha} * length_m) - ({beta} * (scenic_rating * 100))"


def get_baseline_time(fastest_route):
    """Get baseline time from fastest route."""
    return fastest_route["total_time_minutes"]


def calculate_max_allowed_time(baseline_time, max_increase_pct=0.5):
    """Calculate maximum allowed time with constraint."""
    return baseline_time * (1 + max_increase_pct)


def calculate_scenic_route_with_constraint(
    start_point, end_point, preference="balanced", max_time_increase_pct=0.5
):
    """
    Calculate scenic route with time constraint.

    For now returns fastest route as placeholder.
    Real scenic routing will be implemented in next commit.
    """
    fastest_route = routing_service.calculate_fastest_route(start_point, end_point)
    if not fastest_route:
        return None

    baseline_time = get_baseline_time(fastest_route)
    max_allowed_time = calculate_max_allowed_time(baseline_time, max_time_increase_pct)

    alpha, beta = get_preference_coefficients(preference)

    return {
        **fastest_route,
        "preference": preference,
        "baseline_time_minutes": baseline_time,
        "max_allowed_time_minutes": max_allowed_time,
        "coefficients": {"alpha": alpha, "beta": beta},
    }
