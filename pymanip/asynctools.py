import asyncio

def synchronize_generator(async_generator, *args, **kwargs):
    """
    Returns a synchronous generator from an asynchronous generator
    """

    ag = async_generator(*args, **kwargs)
    async def consume_generator():
        r = await ag.__anext__()
        return r
    loop = asyncio.new_event_loop()
    try:
        while True:
            yield loop.run_until_complete(consume_generator())
    except StopAsyncIteration:
        pass
    loop.close()

def synchronize_function(async_func, *args, **kwargs):
    """
    Execute synchronously an asynchronous function
    """

    loop = asyncio.new_event_loop()
    r = loop.run_until_complete(async_func(*args, **kwargs))
    loop.close()
    return r

if __name__ == '__main__':
    
    async def spam(n):
        for i in range(n):
            yield i**n
            await asyncio.sleep(1.0)
    
    
    for x in synchronize_generator(spam, 3):
        print(x)

    async def f(x):
        for i in range(x):
            print(i)
            await asyncio.sleep(0.5)
        return i

    a = synchronize_function(f, 5)
    b = synchronize_function(f, 3)
    print(b)
    
    def sync_spam(n):
        yield from synchronize_generator(spam, n)

    for s in sync_spam(5):
        print(s)