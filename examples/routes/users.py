from autowire import get, post


@get
def fetch(request):
    return {"users": ["Alice", "Bob"]}


@post
def create(request):
    name = request.body["name"]
    return {"created": name}

