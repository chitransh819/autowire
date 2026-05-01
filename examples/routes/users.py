from autowire import get, post


@get("/users", auth=False)
def fetch(request):
    return {"users": ["Alice", "Bob"]}


@post("/users", auth=True)
def create(request):
    name = request.body["name"]
    return {"created": name}
