Fixed the Workers tab's Dense toggle, which flipped its stored setting but left
the list unchanged: the class was set on a wrapper that later render passes
rebuilt without it, so none of the dense styling ever applied.
