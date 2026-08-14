Removed Hermes from the automatic engine update pass: `hermes update` restarts the Hermes gateway service and kills live messaging sessions, so unattended updates must never trigger it.
