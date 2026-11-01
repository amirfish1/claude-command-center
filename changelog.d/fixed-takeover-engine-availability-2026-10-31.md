Session takeover uses a shared lifecycle across engines, with engine adapters
for session observation, same-session delivery and receipt verification.
Auto remains available across engines, and requests rejected by the former
engine restriction recover through the normal adoption path.
Native session settings remain authoritative over ACP facade defaults when
capturing settings for a later same-session resume.
