硅谷大模型技术之FastAPI&SQLAlchemy

（作者：尚硅谷研究院）

版本：V1.0

以 FastAPI 为 Web 框架核心

基于协程的异步编程模式

集成 SQLAlchemy 进行数据交互

依托 Uvicorn 作为异步服务器运行环境

# 1 **协程**

协程（Coroutine）是Python中处理高并发场景的强大工具，尤其在 I/O 密集型任务中表现出色。协程是一种用户态的轻量级线程，它不像线程那样由操作系统调度，而是由程序自身控制，所以也叫微线程。

## 1.1 **线程、进程和协程的对比**

<table><tr><td><p><strong>机制</strong></p></td><td><p><strong>调度者</strong></p></td><td><p><strong>切换开销</strong></p></td><td><p><strong>资源共享</strong></p></td><td><p><strong>适用场景</strong></p></td><td><p><strong>核心优势</strong></p></td><td><p>局限性</p></td></tr><tr><td><p>进程</p></td><td><p>操作系统</p></td><td><p>极大</p><p>(内核级)</p></td><td><p>完全隔离</p></td><td><p>CPU密集型</p></td><td><p>利用多核</p></td><td><p>资源消耗大，数量有限。</p></td></tr><tr><td><p>线程</p></td><td><p>操作系统</p></td><td><p>较大</p><p>(内核级)</p></td><td><p>共享内存 (需加锁)</p></td><td><p>I/O 密集型 (低并发)</p></td><td><p>共享地址空间</p></td><td><p>受GIL限制，且数量过多时调度和同步开销大。</p></td></tr><tr><td><p>协程</p></td><td><p>程序自身<br/>Event Loop</p></td><td><p>极小(函数调用级)</p></td><td><p>共享内存 (无需加锁)</p></td><td><p>I/O 密集型 (极高并发)</p></td><td><p>极高并发，高性能I/O。</p></td><td><p>需要与其配套的异步库或者线程池处理阻塞</p></td></tr></table>

## 1.2 **线程处理IO密集操作的不足**

在处理大量的 I/O 密集型任务（如Web服务要同时处理数千个用户连接）时，线程模型会遇到以下瓶颈：

**切换开销大：** 操作系统在数千个线程之间切换需要消耗大量的 CPU 时间。

**同步复杂：** 线程共享内存，需要使用锁（Lock）来保护共享数据，这增加了程序的复杂性，并可能导致死锁。

**阻塞浪费：** 当一个线程执行到网络等待时，它会阻塞并被挂起。虽然操作系统会切换到另一个线程，但线程自身的切换成本仍然很高，而且往往无法创建足够多的线程来应对极高并发。

## 1.3 **协程的优势**

- 调度者：协程由程序自身进行调用，主动让出CPU给其他协程（通过 await 关键字）
- 轻量级：创建成本极低，一个线程程可以轻松创建数万个协程
- 无锁机制：同一时间只有一个协程运行，无需锁保护共享资源
- 与线程相比，协程避免了操作系统级别的上下文切换开销，只有极小的函数调用开销，在处理大量 I/O 操作时效率更高

## 1.4 **async和await**

在Python 3.5+中，协程通过async和await关键字实现。

### 1.4.1 **定义协程函数：async def**

使用async def关键字定义的函数就是协程函数，它返回一个协程对象。

import asyncio

\# 这是一个协程函数

async def hello():

print("Hello")

\# await 关键字只能在协程函数内部使用

await asyncio.sleep(1) # 模拟I/O操作

print("World")

\# 调用 hello() 不会立即执行，只会返回一个协程对象

coro\_obj = hello()

print(type(coro\_obj)) # 输出: <class 'coroutine'>

### 1.4.2 **挂起与恢复：await关键字**

await是协程的灵魂，它用于暂停当前协程的执行，并等待一个可等待对象（Awaitable，通常是另一个协程或I/O操作）完成。

- 遇到await时：当前协程让出CPU控制权，进入“挂起”状态。
- 事件循环（EventLoop）接管：它会去调度执行其他处于“就绪”状态的协程。
- 等待对象完成后：事件循环会把控制权交还给被挂起的协程，使其从await之后继续执行。

### 1.4.3 **运行协程：事件循环**

事件循环是管理和调度所有协程的核心机制。在Python3.7+中，我们使用asyncio.run()启动事件循环。

import asyncio

import time

async def my\_func(name, delay):

print(f"任务 {name} 开始")

\# 遇到 await，协程让出控制权

await asyncio.sleep(delay)

print(f"任务 {name} 完成")

return f"任务 {name} 结果"

async def main\_sync():

print("--- 串行执行（总耗时 1s + 2s = 3s）---")

\# await 串行执行：必须等待前一个 task 完成，才会开始下一个

await my\_func("A", 1)

await my\_func("B", 2)

if \_\_name\_\_ == "\_\_main\_\_":

start\_time = time.time()

asyncio.run(main\_sync())

print(f"串行耗时: {time.time() - start\_time:.2f}秒")

输出:

--- 串行执行（总耗时 1s + 2s = 3s）---

任务 A 开始

任务 A 完成

任务 B 开始

任务 B 完成

**串行耗时: 3.02秒**

## 1.5 **事件循环(Event Loop) 详述**

事件循环是协程并发模型的核心，它是负责调度、管理和执行所有协程的“心脏”。其工作流程如下：

### 1.5.1 **注册任务**  

注册后，协程就被注册到事件循环中等待执行，如下3种情况都会实现注册。

- **asyncio.run(main\_coro)(顶级注册，启动程序)**

目的：启动整个异步程序。

机制：asyncio.run()负责创建EventLoop，并将传入的顶级协程main\_coro隐式地包装成一个Task并运行。这个Task成为整个EventLoop的主要任务。

用途：作为程序的入口点。

- **asyncio.create\_task(coro)(最常用，实现并发)**

目的：实现并发执行。

机制：将协程对象（coroutine object）立即包装成一个Task，并将其添加到EventLoop的队列中，使其可以立即开始在后台运行，而调用者无需等待。

用途：启动多个任务，然后通过asyncio.gather()等待它们的结果。

- **await coro(隐式注册，实现串行)**

目的：实现串行等待。

机制：当EventLoop正在运行一个协程A，协程A遇到await coro\_B时，EventLoop内部会隐式地将coro\_B注册为一个任务，并开始执行它。但此时EventLoop必须等待coro\_B完成，才能让A协程恢复执行。

用途：确保代码按顺序执行。

### 1.5.2 **启动协程**

事件循环开始运行，选择一个就绪的协程开始执行。

一般是在程序的同步部分（通常是if\_\_name\_\_=="\_\_main\_\_":）调用asyncio.run(main\_coroutine)，并传入您的顶级协程对象。asyncio.run()负责实例化一个EventLoop对象，并将传入的main\_coroutine包装成一个Task，并将其添加到EventLoop中。调用EventLoop对象的run\_until\_complete()方法。此时，EventLoop进入一个永不停止的whileTrue循环，选择第一批就绪任务（即main\_coroutineTask）开始执行。

### 1.5.3 **遇到 await**

协程执行到 await 关键字，表示它即将开始一个耗时的 I/O 等待。让出控制权： 协程主动暂停（挂起），将 I/O 操作注册到事件循环的监控中，然后将控制权交还给事件循环。

### 1.5.4 **切换任务**

事件循环发现当前协程暂停了，它会立即选择下一个处于“就绪”状态的协程继续执行。

### 1.5.5 **I/O完成通知**

当 I/O 操作（例如 3 秒的网络等待）完成后，操作系统会通知事件循环：“这个 I/O 已经准备好了！”

### 1.5.6 **恢复协程**

事件循环将先前挂起的协程重新标记为“就绪”，并在合适的时机让它从上次 await 的地方恢复执行。

### 1.5.7 **终止与清理(TheEnd)**

EventLoop持续运行上述1-6步。

**如果EventLoop发现它的顶级Task(main)已经完成，它立即触发退出机制。**

**此时EventLoop强制取消所有仍在运行的子Task，然后关闭EventLoop，程序退出。**

### 1.5.8 **事件循环伪代码模型**

任务列表 = [Task1, Task2, ...]

while True:

\# 1. 检查 I/O 事件，更新任务状态

\# (例如：Task2 等待的网络响应已到达，将 Task2 标记为 '就绪')

已完成的I O = monitor\_io\_events()

\# 2. 选取就绪任务执行

for task in 就绪任务队列:

task.run\_until\_await() # 运行直到遇到下一个 await

\# 3. 检查是否所有任务都已完成

if not 任务列表 and not 正在等待I O:

break

## 1.6 **实现并发：Task与asyncio.gather()**

协程本身只是一个“可暂停的函数”，要实现并发，必须将其包装成任务(Task)并提交给事件循环。

### 1.6.1 **任务（Task）的概念**

Task是对协程的封装，它自动将协程注册到事件循环中，使其具备被调度和执行的能力。

- 创建：使用asyncio.create\_task(协程对象)。
- 作用：一旦Task被创建，它就立即开始在后台运行（事件循环会调度它），而主协程无需等待。

### 1.6.2 **批量并发执行：asyncio.gather()**

asyncio.gather()是用于并发执行多个协程或任务的常用工具。它等待所有传入的任务都完成后，返回它们的结果。

import asyncio

import time

async def my\_func(name, delay):

print(f"任务 {name} 开始...")

await asyncio.sleep(delay)

print(f"任务 {name} 完成.")

return f"结果 of {name}"

async def main\_concurrent():

print("--- 并发执行（总耗时 max(1s, 2s) = 2s）---")

\# 1. 创建 Task：协程被包装成任务，立即启动在后台运行

task1 = asyncio.create\_task(my\_func("C", 1))

task2 = asyncio.create\_task(my\_func("D", 2))

\# 2. 等待 Task 完成：await asyncio.gather() 等待所有任务结束

\#   注意：这里的 await 只是等待结果，任务本身早已开始并发运行。

results = await asyncio.gather(task1, task2)

print(f"所有结果: {results}")

if \_\_name\_\_ == "\_\_main\_\_":

start\_time = time.time()

asyncio.run(main\_concurrent())

print(f"并发总耗时: {time.time() - start\_time:.2f}秒")

输出:

--- 并发执行（总耗时 max(1s, 2s) = 2s）---

任务 C 开始...

任务 D 开始...

任务 C 完成.

任务 D 完成.

所有结果: ['结果 of C', '结果 of D']

**并发总耗时: 2.01秒**

### 1.6.3 **并发执行原理**

1. task("C", 1) 打印 "开始..."，遇到 await asyncio.sleep(1)，让出控制权。
2. 事件循环切换到 task("D", 2)，打印 "开始..."，遇到 await asyncio.sleep(2)，让出控制权。
3. 事件循环等待外部 I/O 事件（计时器）。
4. 1 秒后，计时器事件完成，事件循环恢复 task("C", 1)，它打印 "完成"。
5. 2 秒后，计时器事件完成，事件循环恢复 task("D", 2)，它打印 "完成"。
6. await asyncio.gather() 收到所有结果并返回。

### 1.6.4 **顶级Task退出，事件循环终止**

import asyncio

import time

async def my\_func(name, delay):

print(f"任务 {name} 开始...")

await asyncio.sleep(delay)

print(f"任务 {name} 完成.")

return f"结果 of {name}"

async def main\_concurrent():

print("--- 并发执行（总耗时 max(1s, 2s) = 2s）---")

asyncio.create\_task(my\_func("C", 1))

asyncio.create\_task(my\_func("D", 2))

if \_\_name\_\_ == "\_\_main\_\_":

start\_time = time.time()

asyncio.run(main\_concurrent())

print(f"并发总耗时: {time.time() - start\_time:.2f}秒")

输出:

--- 并发执行（总耗时 max(1s, 2s) = 2s）---

任务 C 开始...

任务 D 开始...

并发总耗时: 0.00秒

### 1.6.5 **高级控制与协程的限制**

#### 1.6.5.1 **协程的限制：阻塞 I/O**

协程在单线程中运行。如果协程代码中包含阻塞 I/O（如 time.sleep()、requests.get() 等同步库调用），整个事件循环和所有其他协程都会被卡住，并发能力将失效。

**解决方案：隔离阻塞操作**

使用 asyncio.to\_thread()（Python 3.9+）或 loop.run\_in\_executor() 将阻塞代码交给线程池或进程池去执行，从而隔离阻塞，避免卡住主事件循环。

import requests

async def fetch\_blocking\_url(url):

\# 将同步的 requests.get() 扔到另一个线程中执行

response = await asyncio.to\_thread(requests.get, url)

return response.status\_code

#### 1.6.5.2 **超时处理：asyncio.wait\_for()**

用于为单个可等待对象设置明确的超时时间。

async def slow\_task():

await asyncio.sleep(5)

async def main\_timeout():

try:

\# 任务必须在 2 秒内完成

await asyncio.wait\_for(slow\_task(), timeout=2.0)

except asyncio.TimeoutError:

print("任务超时！")

#### 1.6.5.3 **任务取消：Task.cancel()**

用于请求取消一个正在运行的Task。被取消的Task会抛出asyncio.CancelledError异常，协程应捕获此异常进行资源清理。

## 1.7 **实战案例：异步网络请求**

协程最适合的场景是 I/O 密集型任务，网络请求是典型代表。

aiohttp（异步 HTTP 客户端）

import asyncio

import aiohttp

import time

\# 需要安装 aiohttp: pip install aiohttp

async def fetch\_url(session, url):

"""异步请求一个URL并返回结果"""

start\_time = time.time()

\# async with 也是一种 awaitable 对象

async with session.get(url) as response:

\# 在等待响应体时，让出 CPU

await response.text()

response\_time = time.time() - start\_time

return f"{url} 耗时 {response\_time:.2f}秒"

async def main():

urls = [

"https://www.baidu.com",

"https://www.bing.com",

"https://www.github.com"

]

async with aiohttp.ClientSession() as session:

\# 并发请求：总耗时约为单个最慢请求的时间

print("\n--- 并发请求 ---")

start\_time = time.time()

tasks = [fetch\_url(session, url) for url in urls]

results = await asyncio.gather(\*tasks)

for result in results:

print(result)

print(f"并发总耗时: {time.time() - start\_time:.2f}秒")

if \_\_name\_\_ == "\_\_main\_\_":

asyncio.run(main())

# 2 **WSGI和ASGI**

WSGI和ASGI是Python Web开发中两个重要的接口规范，用于定义Web服务器与 Python Web应用之间的通信规则，二者的核心区别在于对异步的支持能力。

## 2.1 **核心定位与目标**

WSGI（Web Server Gateway Interface）是 Python 最早的 Web 服务器与应用接口规范（2003 年提出），仅支持同步操作，主要解决早期 Python Web 框架（如 Flask、Django 旧版本）与服务器的兼容性问题，让同一应用可以运行在不同的 WSGI 服务器上（如 Gunicorn、uWSGI）。

ASGI（Asynchronous Server Gateway Interface）是 WSGI 的异步升级版（2018 年提出），原生支持异步操作，同时兼容 WSGI。设计目标是解决 WSGI 无法高效处理异步任务（如 WebSocket、长轮询）的问题，为 FastAPI、Starlette 等异步框架提供标准接口。

## 2.2 **工作流程差异**

- WSGI 工作流程：

客户端发送请求 → WSGI 服务器接收 → 同步调用应用的 application(environ, start\_response) 函数 → 应用处理后通过 start\_response 返回响应 → 服务器转发响应。

整个过程是同步阻塞的，一个请求未处理完时，对应的线程 / 进程无法处理其他请求。

- ASGI 工作流程：

客户端发送请求 → ASGI 服务器接收 → 将请求封装为事件（如 http.request） → 通过事件循环异步传递给应用 → 应用处理后返回事件（如 http.response） → 服务器转发响应。

等待 I/O 操作（如数据库查询）时，事件循环会切换到其他请求，实现非阻塞处理。

## 2.3 **如何选择**

- 若使用同步框架（如 Flask、Django 3.0 之前版本），需用WSGI服务器（如 Gunicorn）。
- 若使用异步框架（如 FastAPI、Starlette、Django 3.1+ 异步模式），需用**ASGI服务器（如Uvicorn）** 以发挥异步性能。
- 对于需要实时通信（如WebSocket聊天、实时数据推送）的场景，必须使用ASGI。

# 3 **FastAPI**

## 3.1 **FastAPI介绍**

FastAPI 是一个现代、快速（高性能）的Web框架，用于构建API。是建立在Starlette 和Pydantic基础上的。它基于Python 3.7 +的类型提示（type hints）和异步编程（asyncio）能力，使得代码易于编写、阅读和维护。FastAPI 具有自动交互式文档（基于 OpenAPI 规范和 JSON Schema）、数据验证、依赖注入（Dependency Injection）等功能，这些功能使得 API 的开发速度更快、更可靠。

文档： [https://fastapi.tiangolo.com](https://fastapi.tiangolo.com)

源码： [https://github.com/fastapi/fastapi](https://github.com/fastapi/fastapi)

## 3.2 **第一个FastAPI程序**

### 3.2.1 **创建新的项目**

![](images/06c8439a28017ab97842fda2e46cd25a0f0fb80c120c19e89860f0ff7a8ef32c.jpg)

### 3.2.2 **安装依赖**

pip install fastapi

pip install uvicorn

### 3.2.3 **创建一个 main.py 文件并写入以下内容:**

from fastapi import FastAPI

app = FastAPI()

@app.get("/")

def read\_root():

return {"Hello": "World"}

@app.get("/items/{item\_id}")

def read\_item(item\_id: int, q: str | None = None):

return {"item\_id": item\_id, "q": q}

函数前没有加async（同步函数）：

本质：定义一个普通的同步函数，不能在内部使用 await，所有操作都会阻塞当前线程直到完成。

适用场景：函数内部是纯计算逻辑（无 I/O 操作），或使用的库只有同步实现（如某些旧的数据库驱动）。

执行机制：FastAPI 会将同步函数自动放入线程池执行（默认线程池大小为 os.cpu\_count() \* 5），因此多个同步请求会被分配到不同线程并行处理，不会完全阻塞所有请求，也就是说，**可以同时处理多个请求并发，但是在单个线程上，还是会阻塞的**。另外当同步操作耗时过长（如秒级）时，还会因线程池耗尽导致后续请求排队。

### 3.2.4 **在pycharm终端中通过以下命令运行服务器**

uvicorn main:app --reload

uvicorn main:app 命令含义如下:

main：main.py 文件（一个 Python "模块"）。

app：在 main.py 文件中通过 app = FastAPI() 创建的对象。

--reload：让服务器在更新代码后重新启动。仅在开发时使用该选项

### 3.2.5 **浏览器查看效果**

使用浏览器访问 [http://127.0.0.1:8000/items/5?q=iphone17](http://127.0.0.1:8000/items/5?q=iphone17) 。

你将会看到如下 JSON 响应：

![](images/19132ad8b2548710976930faacf16f98672201ab06b01d0527ecda5dcbc6d320.jpg)

你已经创建了一个具有以下功能的 API：

通过 路径 / 和 /items/{item\_id} 接受 HTTP 请求。

以上 路径 都接受 GET 操作（也被称为 HTTP 方法）。

/items/{item\_id} 路径 有一个 路径参数 item\_id 并且应该为 int 类型。

/items/{item\_id} 路径 有一个可选的 str 类型的 查询参数 q

### 3.2.6 **交互式API文档**

现在访问 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)，你会看到自动生成的交互式 API 文档（由 [Swagger UI](https://github.com/swagger-api/swagger-ui)生成）：

![](images/8b0da2cf3927b6183e093ecbd91b39c23839828b3fd2d846b5658b8923b08141.jpg)

### 3.2.7 **直接通过程序启动uvicorn服务**

from fastapi import FastAPI

import uvicorn

app = FastAPI()

@app.get("/")

async def read\_root():

return {"Hello": "World"}

@app.get("/items/{item\_id}")

async def read\_item(item\_id: int, q: str | None = None):

return {"item\_id": item\_id, "q": q}

if \_\_name\_\_ == "\_\_main\_\_":

\# 直接在代码中启动uvicorn服务器

uvicorn.run(

app="main:app",       # 指定要运行的FastAPI应用实例

host="0.0.0.0",  # 允许外部访问（本地可通过127.0.0.1或localhost访问）

port=8000,     # 端口号

reload=True    # 开发模式：代码修改后自动重启（生产环境需去掉）

)

函数前加async def（异步函数）

本质：定义一个异步协程（coroutine），支持在函数内部使用 await 调用其他异步操作（如异步数据库查询、异步 HTTP 请求等）。

适用场景：函数内部包含I/O 密集型操作（如网络请求、文件读写、数据库操作等），且这些操作有对应的异步实现（如 asyncpg 异步数据库、aiohttp 异步 HTTP 库）。

执行机制：FastAPI 会通过 Python 的 asyncio 库处理这些异步请求，这种情况下，请求的处理是“真正的异步”。当遇到 await 时，会暂时让出 CPU 控制权，控制权会被交还给**事件循环**，允许 FastAPI 在当前请求的 I/O 操作（例如，异步数据库查询或 HTTP 请求）等待期间，继续处理其他请求，从而提高并发效率。

**异步和同步函数的选择**

**异步函数（async def）**：FastAPI 会直接在事件循环中运行，遇到 await 时自动切换任务，充分利用异步性能。

**同步函数（def）**：FastAPI 会自动将其放入一个线程池中执行，避免阻塞事件循环（但本质仍是同步执行，无法并行处理）。

**总结：**

只要涉及 I/O 操作（如数据库、网络请求），优先用 async def 并配合异步库，发挥 FastAPI的异步优势。

纯计算逻辑或无异步库可用时，用 def 即可（FastAPI 会自动处理线程池，无需手动管理）。

### 3.2.8 **也可以直接在pycharm中创建fastapi工程**

![](images/34a473de999c689cd195f96295e67dd2c70630281486a3ce38b37337e72207fe.jpg)

### 3.2.9 **程序说明**

#### 3.2.9.1 **导入依赖**

from fastapi import FastAPI

import uvicorn

#### 3.2.9.2 **创建FastAPI实例**

app = FastAPI()

这里的变量 app 会是 FastAPI 类的一个「实例」。

这个实例将是创建你所有 API 的主要交互对象。

#### 3.2.9.3 **创建一个路径操作**

.get("/")以及.get("/items/{item\_id}")

1. **路径**

这里的「路径」指的是 URL 中从第一个 / 起的后半部分。

所以，在一个这样的 URL 中：https://example.com/items/foo，路径是：/items/foo

**「路径」也通常被称为「端点」或「路由」。**

开发 API 时，「路径」是用来分离「关注点」和「资源」的主要手段。

2. **操作**

这里的「操作」指的是一种 HTTP「方法」。

下列之一：

POST

GET

PUT

DELETE

以及更少见的几种：

OPTIONS

HEAD

PATCH

TRACE

在 HTTP 协议中，你可以使用以上的其中一种（或多种）「方法」与每个路径进行通信。在开发 API 时，你通常使用特定的 HTTP 方法去执行特定的行为。

通常使用：

POST：创建数据。

GET：读取数据。

PUT：更新数据。

DELETE：删除数据。

因此，在 OpenAPI 中，每一个 HTTP 方法都被称为「操作」。

#### 3.2.9.4 **定义一个路径操作装饰器**

@app.get("/")以及@app.get("/items/{item\_id}")

告诉 FastAPI 在它下方的函数负责处理如下访问请求：

请求路径为/items/{item\_id},使用 get 操作

你也可以使用其他的操作：

@app.post()

@app.put()

@app.delete()

以及更少见的：

@app.options()

@app.head()

@app.patch()

@app.trace()

#### 3.2.9.5 **定义路径操作函数**

以这个函数为例

@app.get("/items/{item\_id}")

async def read\_item(item\_id: int, q: str| None = None):

路径："/items/{item\_id}"

操作： get。

函数：是位于装饰器下方的函数（位于 @app.get("/") 下方）。

每当 FastAPI接收一个使用GET方法访问**/items/{item\_id}** 的请求时这个函数会被调用。

在这个例子中，它是一个 async 函数。你也可以将其定义为常规函数而不使用 async def

#### 3.2.9.6 **返回内容**

return {"item\_id": item\_id, "q": q}

你可以返回一个 dict、list，像 str、int 一样的单个值，等等。会自动转换为 JSON

#### 3.2.9.7 **启动uvicorn服务器**

if \_\_name\_\_ == "\_\_main\_\_":

\# 直接在代码中启动uvicorn服务器

uvicorn.run(

app="main:app",       # 指定要运行的FastAPI应用实例

host="0.0.0.0",  # 允许外部访问（本地可通过127.0.0.1或localhost访问）

port=8000,     # 端口号

reload=True    # 开发模式：代码修改后自动重启（生产环境需去掉）

)

## 3.3 **路径参数**

### 3.3.1 **案例**

FastAPI 支持使用Python字符串格式化语法声明路径参数（变量）

from fastapi import FastAPI

import uvicorn

app = FastAPI()

@app.get("/items/{item\_id}")

async def read\_item(item\_id):

return {"item\_id": item\_id}

if \_\_name\_\_ == "\_\_main\_\_":

\# 直接在代码中启动uvicorn服务器

uvicorn.run(

app="main:app",       # 指定要运行的FastAPI应用实例

host="0.0.0.0",  # 允许外部访问（本地可通过127.0.0.1或localhost访问）

port=8000,     # 端口号

reload=True    # 开发模式：代码修改后自动重启（生产环境需去掉）

)

这段代码把路径参数item\_id的值传递给路径函数的参数item\_id，运行示例并访问 http://127.0.0.1:8000/items/foo，可获得如下响应：

![](images/8b378d9a94d2566e3560e3b183f59eb8f2d6531e18a5ae2a57510be1a1bbc12a.jpg)

![](images/18e349224932e963e007ecac0e4a9caae7a033b681863697f6912d0ee57f6caa.jpg)

### 3.3.2 **声明路径参数的类型以及类型转换**

使用 Python 标准类型注解，声明路径操作函数中路径参数的类型。类型声明将为函数提供错误检查、代码补全等编辑器支持。

@app.get("/items/{item\_id}")

async def read\_item(item\_id: int):

return {"item\_id": item\_id}

运行示例并访问 [http://127.0.0.1:8000/items/3](http://127.0.0.1:8000/items/3)，返回的响应如下：

![](images/522a7c01f475961d1da69ff114b329d2542ea1a4edc500e84c0c44d57fc7fd39.jpg)

注意，函数接收并返回的值是 3（ int），不是 "3"（str）。

浏览器中传递的数据都是以字符串的形式进行传递的，FastAPI 通过类型声明自动解析请求中的数据，会自动的将路径参数3转换为函数中声明的int类型。

### 3.3.3 **类型校验**

通过浏览器访问 [http://127.0.0.1:8000/items/foo](http://127.0.0.1:8000/items/foo)，接收如下 HTTP 错误信息：

![](images/7cd9904d731b4a5f98f26dd552413f8a5de21e0e45aeeabd652037fe4b9defc6.jpg)

这是因为路径参数 item\_id 的值 （"foo"）的类型不是 int。

值的类型不是int而是浮点数（float）时也会显示同样的错误，比如： [http://127.0.0.1:8000/items/4.2](http://127.0.0.1:8000/items/4.2) ，可以通过api文档看类型约定。

![](images/efe45055edd026602eafa294b8c2970fdb50660bf1d02be9072b33163946a3da.jpg)

### 3.3.4 **参数顺序**

有时，路径操作中的路径是写死的，比如要使用 /users/me 获取当前用户的数据，然后还要使用 /users/{user\_id}，通过用户 ID 获取指定用户的数据。由于路径操作是按顺序依次运行的，因此，一定要在 /users/{user\_id} 之前声明 /users/me

from fastapi import FastAPI

app = FastAPI()

@app.get("/users/me")

async def read\_user\_me():

return {"user\_id": "the current user"}

@app.get("/users/{user\_id}")

async def read\_user(user\_id: str):

return {"user\_id": user\_id}

否则，/users/{user\_id} 将匹配 /users/me，FastAPI 会认为正在接收值为 "me" 的 user\_id 参数。

## 3.4 **请求参数**

### 3.4.1 **案例**

声明的参数不是路径参数时，路径操作函数会把该参数自动解释为查询参数,按照参数的名字进行匹配。

from fastapi import FastAPI

import uvicorn

app = FastAPI()

items\_list = [{"item1": "Foo"}, {"item2": "Bar"}, {"item3": "Baz"}]

@app.get("/items/")

async def read\_item(start: int = 0, limit: int = 10):

return items\_list[start : start + limit]

if \_\_name\_\_ == "\_\_main\_\_":

\# 直接在代码中启动uvicorn服务器

uvicorn.run(

app="main:app",       # 指定要运行的FastAPI应用实例

host="0.0.0.0",  # 允许外部访问（本地可通过127.0.0.1或localhost访问）

port=8000,     # 端口号

reload=True    # 开发模式：代码修改后自动重启（生产环境需去掉）

)

查询字符串是键值对的集合，这些键值对位于URL的?之后，以&分隔。

例如，以下 URL 中：http://127.0.0.1:8000/items/?start=0&limit=2，查询参数为：

start：值为 0 ，limit：值为 2

这些值都是URL的组成部分，因此，它们的类型本应是字符串。

但声明 Python 类型（上例中为 int）之后，这些值就会转换为声明的类型，并进行类型校验。

### 3.4.2 **默认值**

查询参数不是路径的固定内容，它是可选的，还支持默认值。

上例用 start=0 和 limit=10 设定默认值。

访问 URL：[http://127.0.0.1:8000/items/](http://127.0.0.1:8000/items/)

与访问以下地址相同：http://127.0.0.1:8000/items/?start=0&limit=10

但如果访问：http://127.0.0.1:8000/items/?start=20

查询参数的值就是：

start=20：在 URL 中设定的值

limit=10：使用默认值

### 3.4.3 **可选参数**

同理，把默认值设为 None 即可声明可选的查询参数

@app.get("/items/{item\_id}")

async def read\_item(item\_id: str, q: str | None = None):

if q:

return {"item\_id": item\_id, "q": q}

return {"item\_id": item\_id}

本例中，查询参数q是可选的，默认值为 None。FastAPI 可以识别出item\_id 是路径参数，q 不是路径参数，而是查询参数。

### 3.4.4 **查询参数类型转换**

参数还可以声明为 bool 类型，FastAPI 会自动转换参数类型

@app.get("/items/{item\_id}")

async def read\_item(item\_id: str, q: str | None = None, short: bool = False):

item = {"item\_id": item\_id}

if q:

item.update({"q": q})

if not short:

item.update(

{"description": "这是描述信息"}

)

return item

本例中，访问：

http://127.0.0.1:8000/items/foo?short=**1|True|true|on|yes**或其对应的任意大小写形式（大写、首字母大写等），函数接收的 short 参数都是布尔值 True。值为 False 时也一样

注意：必须在红色标记的值内，如果随便传递xx不能转换。

### 3.4.5 **多个路径和查询参数**

FastAPI 可以识别同时声明的多个路径参数和查询参数，而且声明**查询参数**的顺序并不重要。FastAPI 通过参数名进行检测：

from fastapi import FastAPI

import uvicorn

app = FastAPI()

@app.get("/users/{user\_id}/items/{item\_id}")

async def read\_user\_item(

user\_id: int, item\_id: str, q: str | None = None, short: bool = False

):

item = {"item\_id": item\_id, "owner\_id": user\_id}

if q:

item.update({"q": q})

if not short:

item.update(

{"description": "这是描述信息"}

)

return item

if \_\_name\_\_ == "\_\_main\_\_":

\# 直接在代码中启动uvicorn服务器

uvicorn.run(

app="main:app",       # 指定要运行的FastAPI应用实例

host="0.0.0.0",  # 允许外部访问（本地可通过127.0.0.1或localhost访问）

port=8000,     # 端口号

reload=True    # 开发模式：代码修改后自动重启（生产环境需去掉）

)

通过http://127.0.0.1:8000/users/100/items/500访问

![](images/0534dd50776facc6da3352ff698ba699ce55d84c89b99504c519f915981c40e5.jpg)

### 3.4.6 **必选查询参数**

为查询参数的参数声明默认值，该参数就不是必选的了。

如果只想把参数设为可选，但又不想指定参数的值，则要把默认值设为 None。

如果要把查询参数设置为必选，就不要声明默认值。

## 3.5 **请求体传参数**

FastAPI 使用请求体从客户端（例如浏览器）向 API 发送数据。

使用 [Pydantic](https://docs.pydantic.dev/) 模型声明请求体，能充分利用它的功能和优点。

发送数据使用 POST（最常用）、PUT、DELETE、PATCH 等操作。

from fastapi import FastAPI

import uvicorn

from pydantic import BaseModel

\# 定义数据模型类，需要继承 BaseModel 的类。

class Item(BaseModel):

name: str

desc: str | None = None

price: float

app = FastAPI()

@app.post("/items/")

async def create\_item(item: Item):

return item

if \_\_name\_\_ == "\_\_main\_\_":

\# 直接在代码中启动uvicorn服务器

uvicorn.run(

app="main:app",       # 指定要运行的FastAPI应用实例

host="0.0.0.0",  # 允许外部访问（本地可通过127.0.0.1或localhost访问）

port=8000,     # 端口号

reload=True    # 开发模式：代码修改后自动重启（生产环境需去掉）

)

可以通过apidoc查看，请求方式

![](images/5895a4b009ffddacb6fc86cfe372c52d4a53a2031e15080adc7342ada117184e.jpg)

## 3.6 **路由分发**

当项目规模扩大时，将所有路由写在一个文件中会导致代码臃肿、难以维护。路由分发（也叫路由拆分）是将不同功能模块的路由拆分到不同文件，再通过路由注册的方式整合到主应用中，实现代码模块化。

FastAPI 中实现路由分发的核心工具是 APIRouter，它允许你在子模块中定义路由，再将其挂载到主应用

### 3.6.1 **核心组件：APIRouter**

- 作用：在子模块中创建独立的路由集合，类似一个小型 FastAPI 应用。
- 用法：先实例化 APIRouter，用它定义路由，最后通过 app.include\_router() 挂载到主应用。

### 3.6.2 **案例：用户模块与商品模块的路由拆分**

#### 3.6.2.1 **项目结构**

myproject/

├── main.py          # 主应用

├── routers/

│   ├── user.py      # 用户相关路由

│   └── item.py      # 商品相关路由

#### 3.6.2.2 **用户模块路由定义（routers/user.py）**

from fastapi import APIRouter

\# 实例化一个 APIRouter，可指定前缀（所有路由自动加上 /users）

router = APIRouter(

prefix="/users",

tags=["用户管理"]  # 文档中归类为「用户管理」

)

\# 定义用户相关路由

@router.get("/")

def get\_all\_users():

return {"message": "获取所有用户列表"}

@router.get("/{user\_id}")

def get\_user(user\_id: int):

return {"message": f"获取 ID 为 {user\_id} 的用户信息"}

#### 3.6.2.3 **商品模块路由定义（routers/item.py）**

from fastapi import APIRouter

router = APIRouter(

prefix="/items",

tags=["商品管理"]  # 文档中归类为「商品管理」

)

\# 定义商品相关路由

@router.get("/")

def get\_all\_items():

return {"message": "获取所有商品列表"}

@router.get("/{item\_id}")

def get\_item(item\_id: int):

return {"message": f"获取 ID 为 {item\_id} 的商品信息"}

#### 3.6.2.4 **主应用整合路由**

from fastapi import FastAPI

from routers import user, item  # 导入子模块路由

import uvicorn

app = FastAPI(title="路由分发示例")

\# 挂载用户路由：所有 /users 开头的请求由 user.router 处理

app.include\_router(user.router)

\# 挂载商品路由：所有 /items 开头的请求由 item.router 处理

app.include\_router(item.router)

\# 主应用自身也可以定义路由

@app.get("/")

def root():

return {"message": "欢迎访问主页面"}

if \_\_name\_\_ == "\_\_main\_\_":

\# 直接在代码中启动uvicorn服务器

uvicorn.run(

app="main:app",       # 指定要运行的FastAPI应用实例

host="0.0.0.0",  # 允许外部访问（本地可通过127.0.0.1或localhost访问）

port=8000,     # 端口号

reload=True    # 开发模式：代码修改后自动重启（生产环境需去掉）

)

#### 3.6.2.5 **运行与测试**

- 启动主服务
- 主页面：http://127.0.0.1:8000 → 返回主页面信息
- 用户列表：http://127.0.0.1:8000/users → 返回用户列表信息
- 商品详情：http://127.0.0.1:8000/items/100 → 返回 ID=100 的商品信息
- 查看自动文档：http://127.0.0.1:8000/docs，可看到路由按 tags 分类（用户管理、商品管理）。

![](images/f5c6cd32560d2ef75af94d52678bc6c6ae7c2a6c0729131821bdc63e10b78d4e.jpg)

# 4 **SQLAlchemy**

## 4.1 **ORM介绍**

### 4.1.1 **什么是 ORM**

ORM（Object-Relational Mapping，对象关系映射）是一种编程技术，它将数据库中的表结构映射为编程语言中的类，将表中的行映射为类中的对象，让开发者可以用面向对象的方式操作数据库，而无需直接编写 SQL 语句。

### 4.1.2 **ORM的核心价值**

- 屏蔽数据库差异

同一套代码可适配多种数据库（MySQL、PostgreSQL 等），无需修改核心逻辑。

- 简化开发流程

用类、对象、方法替代 SQL 语句，降低数据库操作的学习成本。

- 提高代码可读性

将数据库操作与业务逻辑融合，代码更符合面向对象思维。

- 自动处理类型转换

无需手动转换数据库字段与Python类型（如MySQL的INT与Python的int）。

### 4.1.3 **主流ORM产品对比**

Python生态中有多个成熟的ORM工具，各有侧重，以下是常见产品的对比：

<table><tr><td><p>ORM工具</p></td><td><p>特点</p></td><td><p>适用场景</p></td></tr><tr><td><p><strong>SQLAlchemy</strong></p></td><td><p>功能全面，支持ORM和原生SQL，灵活度极高，文档丰富，生态完善。</p></td><td><p>中大型项目、复杂查询场景、需要跨数据库兼容。</p></td></tr><tr><td><p><strong>Django ORM</strong></p></td><td><p>与 Django 框架深度绑定，开箱即用，简化 CRUD 操作，但灵活性较低。</p></td><td><p>Django 框架开发的 Web 应用。</p></td></tr><tr><td><p><strong>Tortoise-ORM</strong></p></td><td><p>异步 ORM，支持 async/await，与 FastAPI 等异步框架契合度高。</p></td><td><p>异步 Web 应用（如 FastAPI + 异步数据库驱动）。</p></td></tr><tr><td><p><strong>SQLModel</strong></p></td><td><p>基于 SQLAlchemy 和 Pydantic，简化模型定义，兼顾 ORM 和数据验证。</p></td><td><p>FastAPI 项目，追求模型定义简洁性。</p></td></tr></table>

SQLAlchemy是Python中最成熟的ORM 之一，既支持简单的CRUD 操作，也能应对复杂的多表关联、事务管理等场景，且与FastAPI兼容性极佳，是生产环境的首选。

## 4.2 **SQLAlchemy基本架构**

![](images/08400e8168b85007f9f71763689dcee7eeb9e9b345b84cbc072b71d1630959b4.png)

SQLAlchemy的架构分层，从下到上可分为DBAPI 层、SQLAlchemy Core（核心层）、SQLAlchemy ORM（对象关系映射层），各组件作用如下：

### 4.2.1 **DBAPI（数据库通信层）**

DBAPI（Database API）是Python数据库接口规范，是SQLAlchemy与底层数据库通信的 “桥梁”。

不同数据库（如 MySQL、PostgreSQL）有各自的 DBAPI 实现（如 pymysql 是 MySQL 的 DBAPI，psycopg2 是 PostgreSQL 的 DBAPI）。SQLAlchemy 通过适配这些 DBAPI，实现对多种数据库的兼容。

### 4.2.2 **SQLAlchemy Core（核心层）**

Core是SQLAlchemy的 “基础工具集”，提供了SQL表达式语言、数据库连接管理等核心能力，即使不使用 ORM，也能通过 Core 操作数据库。

3. **Schema / Types**

定义数据库的模式（Schema）和数据类型（Types）。

Schema对应数据库的表、列、约束等结构（比如定义一张表有哪些字段、字段类型是什么）。Types封装了数据库支持的数据类型（如 Integer、String、DateTime 等），并提供 Python 类型与数据库类型的映射。

4. **SQL Expression Language**

用Python代码生成SQL语句的 “表达式语言”。

它允许你用面向对象的方式编写 SQL（比如用 table.c.column == value 表示 WHERE column = value），既保留了 SQL 的灵活性，又能避免手写 SQL 带来的语法错误和安全问题（如 SQL 注入）。

5. **Engine**

管理数据库连接的 “引擎”，是与数据库交互的 “入口”。

Engine 负责创建和维护数据库连接，还会集成连接池（Connection Pooling）和方言（Dialect）。

6. **Connection Pooling**

管理数据库连接池，提升数据库操作性能。

连接池会预先创建一批数据库连接并复用，避免频繁创建 / 销毁连接的开销，尤其在高并发场景下能显著提高效率。

7. **Dialect**

处理 “方言” 差异，适配不同数据库的 SQL 语法和特性。

不同数据库（如 MySQL 和 PostgreSQL）的 SQL 语法、函数可能有差异（比如 MySQL 的 LIMIT 和 PostgreSQL 的 LIMIT/OFFSET 用法不同）。Dialect 会对这些差异做 “翻译”，让上层代码能以统一的方式操作不同数据库。

### 4.2.3 **SQLAlchemy ORM（对象关系映射层）**

在Core的基础上，提供象关系映射（ORM）能力，让开发者可以用 “类和对象” 的方式操作数据库（比如用 User 类对应 users 表，用 user = User(name="Alice") 表示新增一条用户记录）。

ORM本质是对Core的 “封装”—— 它会把面向对象的操作（如创建对象、查询对象）自动转换为Core能理解的 SQL 表达式通过，最终 Engine 执行。这样开发者可以更聚焦于业务逻辑，而无需关注底层SQL实现。

### 4.2.4 **核心组件**

- 引擎（Engine）

Engine 是与数据库的连接入口，负责管理连接池和执行 SQL 语句。

- 基类（Base）

所有 ORM 模型的父类，用于统一管理数据库表结构。

- 会话（Session）

用于执行数据库操作的会话对象，负责暂存、提交、回滚数据操作。

## 4.3 **环境准备**

### 4.3.1 **安装依赖**

\# 安装 SQLAlchemy 核心库

pip install sqlalchemy

\# 安装 MySQL 驱动（推荐 pymysql，兼容 Python 3.x）

pip install pymysql

### 4.3.2 **准备 MySQL 环境**

确保本地或远程 MySQL 服务已启动（默认端口 3306）。

创建一个测试数据库（如 fastapi\_db）

## 4.4 **基本操作案例**

### 4.4.1 **在base.py中定义Base基类**

from sqlalchemy.ext.declarative import declarative\_base

\# 生成基类，所有模型需继承该类

Base = declarative\_base()

### 4.4.2 **在models.py中定义ORM模型类（映射MySQL表）**

模型类在定义的时候需要继承Base基类。

模型类对应MySQL中的表，类属性对应表字段，通过 Column 定义字段类型和约束。

from sqlalchemy import Column, Integer, String, ForeignKey, Date

from sqlalchemy.orm import relationship

from demo.base import Base

class Department(Base):

"""部门模型（一对多：一个部门包含多个员工）"""

\_\_tablename\_\_ = "departments"

id = Column(Integer, primary\_key=True, autoincrement=True)

name = Column(String(50), nullable=False, unique=True)  # 部门名称

location = Column(String(100))  # 部门位置（如"北京总部"）

\# 一对多关联员工：返回员工列表，显式指定外键（可选，但更清晰）

employees = relationship(

"Employee",

back\_populates="department",

\# 可选：级联删除（删除部门时自动删除下属员工，根据业务选择）

\# cascade="all, delete-orphan",

\# 可选：懒加载策略，优化查询性能

lazy="selectin"

)

class Employee(Base):

"""员工模型（多对一：多个员工属于一个部门）"""

\_\_tablename\_\_ = "employees"

id = Column(Integer, primary\_key=True, autoincrement=True)

name = Column(String(50), nullable=False)  # 姓名

age = Column(Integer)  # 年龄

hire\_date = Column(Date)  # 入职日期

#添加外键约束，绑定到departments.id

department\_id = Column(

Integer,

ForeignKey("departments.id", ondelete="RESTRICT"),  # 禁止删除有员工的部门

nullable=False  # 员工必须归属一个部门（根据业务可改为True，允许无部门）

)

\# 核心修正：多对一关联，指定uselist=False（返回单个部门对象）

department = relationship(

"Department",

back\_populates="employees",

lazy="joined"  # 可选：立即加载部门数据，减少查询次数

)

字段约束说明：

- primary\_key=True：设为主键。
- autoincrement=True：MySQL 自增（仅整数类型可用）。
- unique=True：唯一索引，避免重复值。
- nullable=False：非空约束（字段必须有值）。

### 4.4.3 **在database.py创建引擎以及会话**

from sqlalchemy import create\_engine

from sqlalchemy.orm import sessionmaker

\# MySQL 连接格式：mysql+pymysql://用户名:密码@主机地址:端口/数据库名

DATABASE\_URL = "mysql+pymysql://root:123456@localhost:3306/fastapi\_db"

\# 创建引擎（echo=True 会打印执行的 SQL，方便调试）

engine = create\_engine(

DATABASE\_URL,

echo=True,  # 是否启用日志输出 开发环境启用，生产环境关闭

pool\_pre\_ping=True  # 连接前检查有效性，避免连接失效

)

\# 创建会话工厂（绑定引擎）

SessionLocal = sessionmaker(

autocommit=False,  # 关闭自动提交，需手动 commit()

bind=engine

)

autoflush=False ：SQLAlchemy 的会话（Session）内部维护了一个 “身份映射”（Identity Map），用于缓存当前会话中操作的对象。当你执行 db.add(new\_user) 时，new\_user 仅被添加到这个会话内存的缓存中，并未同步到数据库（无论是缓冲区还是物理表）。

### 4.4.4 **在main.py中提供建表方法**

创建表结构，通过基类的 create\_all 方法在 MySQL 中创建表（仅需执行一次）

from demo.base import Base

from demo.database import engine

from demo.models import Employee,Department **#必须要加**

\# 创建表

def create\_table():

print("注册的表名:", Base.metadata.tables.keys())

\# 创建所有模型对应的表

Base.metadata.create\_all(bind=engine)

print("表创建成功")

if \_\_name\_\_ == '\_\_main\_\_':

create\_table()

执行后，MySQL 中会生成 employees 和 departments表，包含定义的字段和约束。

### 4.4.5 **新增数据（Create）**

def insert\_data():

\# 获取数据库会话

db = SessionLocal()

try:

\# =========== 第一步：新增部门 =============

new\_dept = Department(

name="研发部",  # 部门名称（唯一）

location="北京总部"  # 部门位置

)

db.add(new\_dept)  # 将部门对象加入会话

db.commit()  # 提交到数据库（执行 INSERT 语句）

db.refresh(new\_dept)  # 刷新对象，获取自增的 id 等字段

\# =========== 第二步：新增关联的员工 ===========

\# 员工1：关联上面创建的研发部

emp1 = Employee(

name="张三",

age=30,

hire\_date=date(2023, 1, 1),  # 入职日期（datetime.date 类型）

department\_id=new\_dept.id  # 关联部门 ID（外键）

)

\# 员工2：同部门的另一个员工

emp2 = Employee(

name="李四",

age=28,

hire\_date=date(2023, 3, 15),

department\_id=new\_dept.id

)

\# 批量添加员工（也可逐个 add）

db.add\_all([emp1, emp2])

db.commit()  # 提交员工数据

\# 刷新员工对象，获取自增 ID

db.refresh(emp1)

db.refresh(emp2)

\# ===================== 输出结果 =====================

print(f"新增部门：ID={new\_dept.id}，名称={new\_dept.name}，位置={new\_dept.location}")

print(f"新增员工1：ID={emp1.id}，姓名={emp1.name}，所属部门={new\_dept.name}")

print(f"新增员工2：ID={emp2.id}，姓名={emp2.name}，所属部门={new\_dept.name}")

\# 验证关联关系（通过 ORM 关联查询）

print("\n【验证关联关系】")

\# 从员工查部门

print(f"员工{emp1.name}的部门名称：{emp1.department.name}")

\# 从部门查员工

dept\_employees = new\_dept.employees

print(f"部门{new\_dept.name}的员工列表：{[emp.name for emp in dept\_employees]}")

except Exception as e:

db.rollback()  # 出错时回滚

print(f"新增失败：{e}")

finally:

db.close()  # 关闭会话

### 4.4.6 **删除数据（Delete）**

def delete\_data():

\# 获取会话

session = SessionLocal()

try:

\# 删除单个员工

emp = session.query(Employee).filter(Employee.name == "李四").first()

if emp:

session.delete(emp)

session.commit()

print(f"已删除员工：{emp.name}")

except Exception as e:

session.rollback()  # 出错时回滚

print(f"刪除失败：{e}")

finally:

session.close()  # 关闭会话

### 4.4.7 **修改数据（Update）**

def update\_data():

\# 获取会话

session = SessionLocal()

try:

\# 修改员工年龄

emp = session.query(Employee).filter(Employee.name == "张三").first()

if emp:

emp.age = 31  # 直接修改属性

session.commit()  # 提交更新

print(f"修改后 {emp.name} 的年龄：{emp.age}")

except Exception as e:

session.rollback()  # 出错时回滚

print(f"修改失败：{e}")

finally:

session.close()  # 关闭会话

### 4.4.8 **查询数据（Read）**

def read\_data():

\# 获取会话

session = SessionLocal()

try:

\# ======按主键查询 get========

\#  查询id=1的部门

dept = session.get(Department,1)

print(f"部门 ID=1：{dept.name}（{dept.location}）")

\# ======过滤（filter）查询========

\# 查询研发部的所有员工

rd\_employees = session.query(Employee).filter(

Employee.department\_id == 1  # 按部门ID过滤

).all()

print("研发部员工：", [emp.name for emp in rd\_employees])  # 输出：['张三']

\# 查询年龄>30的员工

old\_employees = session.query(Employee).filter(Employee.age > 30).all()

print("年龄>30的员工：", [emp.name for emp in old\_employees])  # 输出：['张三', '王五']

\# ======逻辑运算（and\_/or\_）查询========

from sqlalchemy import and\_, or\_

\# 年龄30-40且属于研发部的员工（and\_）

emp = session.query(Employee).filter(

and\_(Employee.age.between(30, 40), Employee.department\_id == 1)

).first()

print("符合条件的员工：", emp.name)  # 输出：张三

\# 属于市场部或年龄>32的员工（or\_）

emps = session.query(Employee).filter(

or\_(Employee.department\_id == 2, Employee.age > 32)

).all()

print("符合条件的员工：", [emp.name for emp in emps])  # 输出：['张三', '王五']

\# ======表连接（join）查询========

\# 内连接：查询员工及其所属部门名称

\# 语法：query(主表, 关联表).join(关联表, 连接条件)

result = session.query(Employee, Department).join(

Department, Employee.department\_id == Department.id

).all()

for emp, dept in result:

print(f"员工 {emp.name} 属于 {dept.name}")

\# 输出：

\# 员工 张三 属于 研发部

\# 员工 王五 属于 市场部

\# ======预加载关联数据（joinedload）查询========

from sqlalchemy.orm import joinedload

\# 加载员工时同时加载部门信息（避免多次查询）

employees = session.query(Employee).options(

joinedload(Employee.department)  # 预加载关联的 department

).all()

\# 直接访问关联数据，不会触发新查询

for emp in employees:

print(f"{emp.name} 的部门：{emp.department.name}")

\# 输出：

\# 张三 的部门：研发部

\# 王五 的部门：市场部

\# ======子查询（subquery）========

from sqlalchemy import func

#子查询：统计每个部门的员工数，再查询员工数>0的部门

\# 步骤1：创建子查询（统计部门员工数）

dept\_emp\_count = session.query(

Employee.department\_id,

func.count(Employee.id).label("count")  # 别名 count

).group\_by(Employee.department\_id).subquery()  # 转为子查询

\# 步骤2：主查询（关联子查询结果）

depts = session.query(Department).join(

dept\_emp\_count, Department.id == dept\_emp\_count.c.department\_id

).filter(dept\_emp\_count.c.count > 0).all()  # 筛选员工数>0的部门

print("有员工的部门：", [dept.name for dept in depts])  # 输出：['研发部', '市场部']

\# ======去重（distinct）========

\# 查询所有有员工的部门位置（去重）

locations = session.query(Department.location).join(

Employee

).distinct().all()  # distinct() 去重

print("部门位置：", [loc[0] for loc in locations])  # 输出：['北京', '广州']

\# ======结果获取（first/all）========

\# first()：返回第一条结果（适合唯一查询）

first\_emp = session.query(Employee).first()

print("第一个员工：", first\_emp.name)  # 输出：张三

\# all()：返回所有结果（列表）

all\_depts = session.query(Department).all()

print("所有部门：", [dept.name for dept in all\_depts])  # 输出：['研发部', '市场部']

except Exception as e:

session.rollback()  # 出错时回滚

print(f"查询失败：{e}")

finally:

session.close()  # 关闭会话

## 4.5 **关联关系**

在 SQLAlchemy 中，关联关系（Relationship） 用于定义不同模型（表）之间的业务关联（如一对一、一对多、多对多），通过 relationship 函数实现，配合字段定义（如外键）可实现对象化的关联查询和操作。

### 4.5.1 **常见的关联关系**

关联关系本质是映射数据库中表与表的关系，SQLAlchemy 提供了 4 种基础关联类型：

<table><tr><td><p>关联类型</p></td><td><p>场景示例</p></td><td><p>数据库实现</p></td></tr><tr><td><p>一对多（One-to-Many）</p></td><td><p>一个用户拥有多个商品</p></td><td><p>子表通过外键关联主表</p></td></tr><tr><td><p>多对一（Many-to-One）</p></td><td><p>多个商品属于一个用户</p></td><td><p>同上（一对多的反向视角）</p></td></tr><tr><td><p>一对一（One-to-One）</p></td><td><p>一个用户对应一个个人资料</p></td><td><p>子表外键设为唯一（unique=True）</p></td></tr><tr><td><p>多对多（Many-to-Many）</p></td><td><p>多个学生选修多个课程</p></td><td><p>通过中间表关联两个表</p></td></tr></table>

### 4.5.2 **核心配置参数（relationship 函数）**

relationship 函数是定义关联关系的核心，常用参数如下

<table><tr><td><p>参数</p></td><td><p>作用</p></td><td><p>示例</p></td></tr><tr><td><p>argument</p></td><td><p>必选，指定关联的目标模型（类或字符串）</p></td><td><p>relationship("Item")</p></td></tr><tr><td><p>back_populates</p></td><td><p>双向关联时，指定反向关联的字段名（显式定义双向关系）</p></td><td><p>back_populates="owner"</p></td></tr><tr><td><p>backref</p></td><td><p>简化双向关联，自动为目标模型添加反向关联字段（隐式定义）</p></td><td><p>backref="owner"</p></td></tr><tr><td><p>foreign_keys</p></td><td><p>显式指定关联的外键字段（多外键场景下必用）</p></td><td><p>foreign_keys=[Item.owner_id]</p></td></tr><tr><td><p>cascade</p></td><td><p>级联操作规则（如保存、删除关联数据）</p></td><td><p>cascade="all, delete-orphan"</p></td></tr><tr><td><p>lazy</p></td><td><p>关联数据的加载方式（控制查询性能）</p></td><td><p>lazy="selectin"（一次性加载）</p></td></tr><tr><td><p>uselist</p></td><td><p>控制是否为集合（True 表示一对多，False 表示一对一）</p></td><td><p>uselist=False（一对一）</p></td></tr><tr><td><p>secondary</p></td><td><p>多对多关系中，指定中间表</p></td><td><p>secondary=user_course</p></td></tr><tr><td><p>primaryjoin/secondaryjoin</p></td><td><p>复杂关联时，显式定义表连接条件（默认自动生成）</p></td><td><p>primaryjoin=(User.id == Item.owner_id)</p></td></tr></table>

### 4.5.3 **具体关联类型的配置方式**

#### 4.5.3.1 **一对多（One-to-Many）与多对一（Many-to-One）**

最常用的关联类型，以 “用户（User）- 商品（Item）” 为例：

from sqlalchemy import Column, Integer, String, ForeignKey

from sqlalchemy.orm import relationship, DeclarativeBase

class Base(DeclarativeBase):

pass

class User(Base):

\_\_tablename\_\_ = "users"

id = Column(Integer, primary\_key=True)

username = Column(String(50))

\# 一对多：用户拥有多个商品（通过 back\_populates 与 Item.owner 双向关联）

items = relationship(

"Item",  # 关联目标模型

back\_populates="owner",  # 反向关联字段（Item 中的 owner）

lazy="selectin",  # 加载方式：查询用户时同时加载商品

cascade="save-update"  # 级联保存：保存用户时自动保存关联商品

)

class Item(Base):

\_\_tablename\_\_ = "items"

id = Column(Integer, primary\_key=True)

title = Column(String(100))

owner\_id = Column(Integer, ForeignKey("users.id"))  # 外键关联用户表

\# 多对一：商品属于一个用户（反向关联）

owner = relationship(

"User",  # 关联目标模型

back\_populates="items"  # 反向关联字段（User 中的 items）

)

使用示例

\# 获取会话

session = SessionLocal()

\# 创建用户和商品

user = User(username="test")

item = Item(title="商品1", owner=user)  # 直接关联用户

user.items.append(item)  # 或通过用户的 items 列表添加

session.add(user)

session.commit()

\# 查询关联数据

user = session.query(User).first()

print(user.items)  # 获取用户的所有商品（因 lazy="selectin" 已加载）

item = session.query(Item).first()

print(item.owner.username)  # 获取商品所属用户的用户名

#### 4.5.3.2 **一对一（One-to-One）**

在一对多基础上，通过 uselist=False 限制关联为单个对象，以 “用户（User）- 个人资料（Profile）” 为例：

class Base(DeclarativeBase):

pass

class User(Base):

\_\_tablename\_\_ = "users"

id = Column(Integer, primary\_key=True)

username = Column(String(50))

\# 一对一：用户对应一个资料（uselist=False 表示非集合）

profile = relationship(

"Profile",

back\_populates="user",

uselist=False,  # 关键：关联结果为单个对象（非列表）

cascade="all, delete-orphan"  # 删除用户时删除资料

)

class Profile(Base):

\_\_tablename\_\_ = "profiles"

id = Column(Integer, primary\_key=True)

bio = Column(String(200))  # 个人简介

user\_id = Column(Integer, ForeignKey("users.id"), unique=True)  # 外键唯一

\# 反向关联用户

user = relationship("User", back\_populates="profile")

关键点：

- 子表（Profile）的外键需设 unique=True，确保一个用户只对应一个资料；
- 主表（User）的 relationship 需设 uselist=False，表示关联结果是单个对象（而非列表）。

使用示例

\# 获取会话

session = SessionLocal()

\# 方式1：先创建用户，再创建资料并关联

user1 = User(username="alice")

session.add(user1)

session.commit()  # 先提交用户，获取 ID

profile1 = Profile(bio="喜欢读书", user\_id=user1.id)  # 通过 user\_id 关联

session.add(profile1)

session.commit()

\# 方式2：直接通过 relationship 关联（更简洁）

user2 = User(

username="bob",

profile=Profile(bio="热爱运动")  # 直接嵌套 Profile 对象

)

session.add(user2)

session.commit()  # 自动同步 user\_id

#### 4.5.3.3 **多对多（Many-to-Many）**

需要通过中间表关联两个模型，以 “学生（Student）- 课程（Course）” 为例：

from sqlalchemy import Column, Integer, String, ForeignKey

from sqlalchemy.orm import relationship, DeclarativeBase

class Base(DeclarativeBase):

pass

from sqlalchemy import Table  # 用于定义中间表

\# 1. 定义中间表（无需模型类，直接用 Table 定义）

student\_course = Table(

"student\_course",  # 中间表名

Base.metadata,

Column("student\_id", Integer, ForeignKey("students.id"), primary\_key=True),

Column("course\_id", Integer, ForeignKey("courses.id"), primary\_key=True)

)

class Student(Base):

\_\_tablename\_\_ = "students"

id = Column(Integer, primary\_key=True)

name = Column(String(50))

\# 多对多：学生选修多个课程（通过 secondary 指定中间表）

courses = relationship(

"Course",

secondary=student\_course,  # 关联中间表

back\_populates="students",

lazy="selectin"

)

class Course(Base):

\_\_tablename\_\_ = "courses"

id = Column(Integer, primary\_key=True)

name = Column(String(100))

\# 反向关联：课程包含多个学生

students = relationship(

"Student",

secondary=student\_course,

back\_populates="courses"

)

使用示例：

\# 获取会话

session = SessionLocal()

\# 创建学生和课程

student1 = Student(name="张三")

student2 = Student(name="李四")

course1 = Course(name="数学")

course2 = Course(name="英语")

\# 建立关联

student1.courses = [course1, course2]

student2.courses = [course1]

session.add\_all([student1, student2, course1, course2])

session.commit()

\# 查询：学生选修的课程

print(student1.courses)  # [Course(name="数学"), Course(name="英语")]

\# 查询：课程包含的学生

print(course1.students)  # [Student(name="张三"), Student(name="李四")]

### 4.5.4 **关键参数详解**

#### 4.5.4.1 **双向关联：back\_populates vs backref**

- **back\_populates（推荐**）：显式在两个模型中定义反向关联，逻辑清晰。

例：User.items 设 back\_populates="owner"，Item.owner 设 back\_populates="items"。

- **backref：** 简化写法，在一个模型中定义即可自动为另一个模型生成反向字段。

例：User.items = relationship("Item", backref="owner")，则 Item 会自动拥有 owner 字段。

#### 4.5.4.2 **级联操作（cascade）**

控制主对象操作对关联对象的影响，常用规则：

- **save-update：** 保存主对象时自动保存关联对象（默认值）；
- **delete：** 删除主对象时自动删除关联对象；
- **delete-orphan：** 关联对象与主对象解除关联时自动删除（仅用于一对多）；
- **all：** 包含 save-update, merge, refresh-expire, expunge, delete。

例：cascade="all, delete-orphan" 表示 “全量级联 + 解除关联时删除”。

#### 4.5.4.3 **加载方式（lazy）**

控制关联数据的查询时机，影响性能：

- **select（默认）**：访问关联属性时才查询（可能产生 N+1 问题）；
- **selectin：** 查询主对象时，通过 IN 语句一次性加载所有关联数据（推荐）；
- **joined：** 通过 JOIN 语句与主对象一起加载（适合一对一）；
- **subquery：** 通过子查询加载关联数据；
- **dynamic：** 返回查询对象（可继续添加过滤条件，适合大数据量）。
- 关联关系是 SQLAlchemy ORM 的核心，通过 relationship 函数定义，配合外键或中间表实现；
- 按业务场景选择关联类型（一对多、一对一、多对多），并配置双向关联（back\_populates）；
- 合理设置 cascade（级联）和 lazy（加载方式），平衡代码简洁性和性能；
- 多对多关系需通过中间表（Table 实例）实现，无需定义模型类。

### 4.5.5 **总结**

## 4.6 **sqlacodegen通过表生成类**

sqlacodegen 是一个实用工具，能根据现有数据库表结构（或 SQL 语句）自动生成 SQLAlchemy 模型类，省去手动编写模型的麻烦，尤其适合已有数据库的项目迁移。

它支持多种数据库（MySQL、PostgreSQL、SQLite 等），生成的模型类包含表名、字段类型、主键、外键、索引等完整信息。

### 4.6.1 **安装依赖**

\# 直接安装（支持 SQLAlchemy 1.4+ 和 2.0+）

pip install sqlacodegen

\# 如果需要连接特定数据库，需安装对应驱动（以 MySQL 为例）

pip install pymysql  # MySQL 驱动

### 4.6.2 **准备数据库表（SQL）**

先在数据库中创建 departments（部门）和 employees（员工）表，包含一对多关系（一个部门有多个员工）：

-- 部门表

CREATE TABLE departments (

id INT PRIMARY KEY AUTO\_INCREMENT,

name VARCHAR(50) NOT NULL UNIQUE COMMENT '部门名称',

location VARCHAR(100) COMMENT '部门位置',

created\_at DATETIME DEFAULT CURRENT\_TIMESTAMP

);

-- 员工表（关联部门）

CREATE TABLE employees (

id INT PRIMARY KEY AUTO\_INCREMENT,

name VARCHAR(50) NOT NULL COMMENT '员工姓名',

age INT COMMENT '年龄',

hire\_date DATE COMMENT '入职日期',

department\_id INT COMMENT '所属部门ID',

FOREIGN KEY (department\_id) REFERENCES departments(id) ON DELETE SET NULL,

INDEX idx\_dept (department\_id)  -- 部门索引，优化查询

);

### 4.6.3 **创建table\_2\_models.py用于接收创建的模型类**

### 4.6.4 **在gen.py中进行测试**

import subprocess

import sys

from sqlalchemy import create\_engine

from sqlalchemy.orm import sessionmaker, Session

from demo1.table\_2\_models import Departments, Employees

\# 创建数据库引擎

db\_host = "localhost"

db\_port = 3306

db\_name = "fastapi\_db"

db\_user\_name = "root"

db\_password = "123456"

url = f"mysql+pymysql://{db\_user\_name}:{db\_password}@{db\_host}:{db\_port}/{db\_name}?charset=utf8mb4"

engine = create\_engine(url, echo=True)

\# 配置会话工厂

engine = create\_engine(url)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

\# 生成模型类

def table\_2\_model(run=False):

"""将数据库表映射为Python类"""

if not run:

return

output\_path = "table\_2\_models.py"

venv\_python = sys.executable  # 若PyCharm使用虚拟环境，这里会返回.venv下的python.exe

print("当前使用的Python路径：", venv\_python)  # 确认输出是.venv/Scripts/python.exe

cmd = [venv\_python, "-m", "sqlacodegen", url]

result = subprocess.run(cmd, capture\_output=True, text=True, encoding="utf-8")

\# 打印执行结果（定位问题核心）

print("=== 命令执行结果 ===")

print(f"返回码（0=成功，非0=失败）：{result.returncode}")

print(f"标准输出：\n{result.stdout}")

print(f"错误输出：\n{result.stderr}")  # 重点看这里，会显示失败原因

with open(output\_path, "w", encoding="utf-8") as f:

f.write(result.stdout)

\# 向员工和部门表中插入数据

def insert\_dept\_emp():

\# 1. 创建员工对象（用关键字参数，日期转换为date类型）

emp = Employees(

id=100,  # 显式指定参数名

name='zs',

age=20,

\# hire\_date=date(2025, 10, 10)  # 字符串转date对象

hire\_date='2025-10-10'  # 字符串转date对象

)

\# 2. 创建部门对象（关键字参数，日期转换为datetime类型）

dept = Departments(

id=10,

name='研发部',

location='北京',

created\_at='2025-10-10',  # 字符串转datetime对象

employees=[emp]  # 关联员工

)

\# 3. 插入数据库

with Session(engine) as session:

session.add(dept)

try:

session.commit()

print(f"插入成功！部门ID：{dept.id}，员工ID：{emp.id}")

except Exception as e:

session.rollback()

print(f"插入失败：{e}")

if \_\_name\_\_ == "\_\_main\_\_":

\# table\_2\_model(True)

insert\_dept\_emp()

# 5 **FastAPI与SQLAlchemy结合案例（扩展）**

## 5.1 **项目结构**

myproject/

├── fastapi\_sqlalchemy.py         # FastAPI 主应用（接口定义）

├── base.py          # 模型类基类

├── database.py      # 数据库配置（引擎、会话）

└── models.py        # SQLAlchemy 模型（映射 MySQL 表）

## 5.2 **复用第四章定义的base.py、table\_2\_models.py、database.py**

## 5.3 **创建fastapi\_sqlalchemy测试**

\# main.py

import uvicorn

from fastapi import FastAPI, Depends, HTTPException

from sqlalchemy.orm import Session

from datetime import date

from table\_2\_models import Departments, Employees

from database import SessionLocal, engine

app = FastAPI()

\# 依赖项：获取数据库会话

def get\_db():

db = SessionLocal()

try:

yield db

finally:

db.close()

\# 部门相关接口

@app.post("/departments/")

def create\_department(name: str, location: str, db: Session = Depends(get\_db)):

db\_department = Departments(name=name, location=location)

db.add(db\_department)

db.commit()

db.refresh(db\_department)

return db\_department

@app.get("/departments/")

def read\_departments(db: Session = Depends(get\_db)):

return db.query(Departments).all()

@app.get("/departments/{department\_id}")

def read\_department(department\_id: int, db: Session = Depends(get\_db)):

department = db.query(Departments).filter(Departments.id == department\_id).first()

if not department:

raise HTTPException(status\_code=404, detail="Department not found")

return department

if \_\_name\_\_ == "\_\_main\_\_":

\# 直接在代码中启动uvicorn服务器

uvicorn.run(

app="fastapi\_sqlalchemy:app",       # 指定要运行的FastAPI应用实例

host="0.0.0.0",  # 允许外部访问（本地可通过127.0.0.1或localhost访问）

port=8000,     # 端口号

reload=True    # 开发模式：代码修改后自动重启（生产环境需去掉）

)

## 5.4 **借助apidoc测试接口**

![](images/592c102c4b3f0e4c5a8bd413b1ff05e40ae2f6e28ee043ee9bd839746a8c430b.jpg)