"""How a running job talks to the UI, through Redis (the worker and the browser never
talk directly):

- log.py: the job's printed lines go to `job:{id}:log`; the API streams them to the UI.
- approval.py: a Yes / No question for a tool goes to the UI; the worker waits for the answer.
"""
