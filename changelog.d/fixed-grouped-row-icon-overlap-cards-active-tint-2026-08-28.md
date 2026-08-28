fixed: object-grouped session rows had a hardcoded 29px indent smaller than the session icon's own box, so long titles overlapped the icon; now share the same content gutter as every other row
fixed: the Cards row-delineation style had its own `!important` override forcing the selected row back to plain gray, hiding the new active-row purple tint entirely; now tints under Cards mode too
