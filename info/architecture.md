app/
  main.py                      # entrypoint
  config.py                    # env + config loading
  agents/
    base_agent.py
    bank_teller.py
    customer.py
  orchestration/
    conversation_manager.py    # lifecycle + termination
  reliability/
    metrics.py                 # SLIs + run summary
    logging.py                 # structured logs
    slis.py                    # definitions + helpers
