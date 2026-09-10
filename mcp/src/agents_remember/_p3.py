def a(ctx, *, attempt, catalog_revision, catalog_manifest_id, dependency_decision, x):
    return ctx


def b(ctx, *, attempt, catalog_revision, catalog_manifest_id, dependency_decision):
    return ctx


def c(ctx, *, x1, x2, x3, x4, **kwargs):
    return ctx


def d(ctx, x1, x2, x3, x4, *, x5=None, **kwargs):
    return ctx
