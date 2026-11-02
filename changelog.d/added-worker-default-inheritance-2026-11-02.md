Let worker defaults explicitly follow new-session defaults, with effective
worker fields saved atomically for queue launchers. Independent worker settings
are preserved for return; queue pins, fallback policies and active runs keep
their existing behavior.
