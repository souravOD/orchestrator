import traceback
try:
    from orchestrator import api
    print("API module loaded OK")
    print("App:", api.app)
except Exception as e:
    traceback.print_exc()
