def span_kwonly(span_kind, started_at, started_epoch_millis, wall_millis, active_millis, **extra):
    return (span_kind, started_at, started_epoch_millis, wall_millis, active_millis, extra)


def with_6(ctx, *, a, b, c, d, **extra):
    return (ctx, a, b, c, d, extra)
