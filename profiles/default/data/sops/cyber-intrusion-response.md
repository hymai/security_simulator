# SOP: Control Network Intrusion Response

Applies when intrusion into the SCADA/OT control network is suspected — for
example anomalous outbound traffic from an engineering workstation or an
unexpected PLC configuration change.

## Step 1 — Detection and initial triage
- **SCC Operator**: Log the alert and notify the on-call IT Security Analyst
  without delay.
- **IT Security Analyst**: Confirm the indicator (review workstation logs and
  network flows) and determine whether the OT network is affected.
- **IT Security Analyst**: Identify the affected engineering workstation and its
  network segment.

## Step 2 — Containment
- **IT Security Analyst**: Isolate the affected workstation from the OT network
  (disable its switch port) while preserving it for forensics.
- **Duty Manager**: Authorize containment actions and assess operational impact
  on the control system.
- **SCC Operator**: Record all actions and timings in the incident log.

## Step 3 — Eradication, recovery and reporting
- **IT Security Analyst**: Remove the attacker's foothold and validate PLC
  configuration against the known-good baseline before reconnection.
- **Duty Manager**: Approve return to normal operations once integrity is
  confirmed.
- **Duty Manager**: Report the incident to the site manager and the national
  cyber authority as required.
