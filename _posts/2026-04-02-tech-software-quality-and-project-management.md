---
layout: post
title: "軟體品質與專案管理：架構與流程"
date: 2026-04-02
categories: tech
tags:
  - software quality
  - project management
  - C
  - python
  - CI/CD
  - testing
---

在軟體開發中，「能跑就好」和「品質穩定」是兩個截然不同的境界。
本文從架構與流程兩個角度，介紹如何在 C 和 Python 專案中建立軟體品質與專案管理的基礎。

---

## 一、什麼是軟體品質？

軟體品質通常可以從以下幾個面向衡量：

- **功能正確性**：程式是否如預期運作
- **可維護性**：他人（或未來的自己）是否能快速理解與修改
- **可靠性**：在異常情況下是否能正確處理
- **效能**：執行速度與資源使用是否在可接受範圍

---

## 二、專案管理架構

一個常見的軟體專案管理架構如下：

```
project/
├── src/          # 原始碼
├── tests/        # 測試
├── docs/         # 文件
├── scripts/      # 輔助腳本
└── README.md
```

不論是 C 還是 Python 專案，這個基本結構都適用，差異在於工具鏈的選擇。

---

## 三、C 專案的品質流程

### 3.1 專案結構範例

```
c_project/
├── src/
│   ├── main.c
│   └── utils.c
├── include/
│   └── utils.h
├── tests/
│   └── test_utils.c
└── Makefile
```

### 3.2 撰寫可測試的程式碼

避免把所有邏輯塞進 `main()`，將功能拆分成獨立函式：

```c
// include/utils.h
#ifndef UTILS_H
#define UTILS_H

int add(int a, int b);
int clamp(int value, int min, int max);

#endif
```

```c
// src/utils.c
#include "utils.h"

int add(int a, int b) {
    return a + b;
}

int clamp(int value, int min, int max) {
    if (value < min) return min;
    if (value > max) return max;
    return value;
}
```

### 3.3 撰寫單元測試

C 常用的測試框架有 `Unity`、`CUnit`，這裡用最簡單的 assert 示範：

```c
// tests/test_utils.c
#include <assert.h>
#include <stdio.h>
#include "utils.h"

void test_add() {
    assert(add(1, 2) == 3);
    assert(add(-1, 1) == 0);
    printf("test_add passed\n");
}

void test_clamp() {
    assert(clamp(5, 0, 10) == 5);
    assert(clamp(-1, 0, 10) == 0);
    assert(clamp(11, 0, 10) == 10);
    printf("test_clamp passed\n");
}

int main() {
    test_add();
    test_clamp();
    return 0;
}
```

### 3.4 使用 Makefile 管理建置流程

```makefile
CC = gcc
CFLAGS = -Wall -Wextra -Iinclude

all: build

build:
	$(CC) $(CFLAGS) src/utils.c src/main.c -o app

test:
	$(CC) $(CFLAGS) src/utils.c tests/test_utils.c -o test_runner
	./test_runner

clean:
	rm -f app test_runner
```

執行測試只需要：
```bash
make test
```

---

## 四、Python 專案的品質流程

### 4.1 專案結構範例

```
python_project/
├── src/
│   └── utils.py
├── tests/
│   └── test_utils.py
├── requirements.txt
└── pyproject.toml
```

### 4.2 撰寫模組化的程式碼

```python
# src/utils.py

def add(a: int, b: int) -> int:
    return a + b

def clamp(value: int, min_val: int, max_val: int) -> int:
    return max(min_val, min(max_val, value))
```

### 4.3 使用 pytest 撰寫測試

```python
# tests/test_utils.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils import add, clamp

def test_add():
    assert add(1, 2) == 3
    assert add(-1, 1) == 0

def test_clamp():
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(11, 0, 10) == 10
```

執行測試：
```bash
pytest tests/
```

### 4.4 加入靜態型別檢查與風格檢查

```bash
# 安裝工具
pip install mypy ruff

# 型別檢查
mypy src/utils.py

# 風格檢查與自動修正
ruff check src/
ruff format src/
```

---

## 五、CI/CD 流程整合

不論是 C 還是 Python，都可以用 GitHub Actions 把測試自動化：

```yaml
# .github/workflows/test.yml
name: Run Tests

on: [push, pull_request]

jobs:
  test-python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install pytest
      - name: Run tests
        run: pytest tests/

  test-c:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Build and test
        run: make test
```

每次 push 都會自動跑測試，確保新的改動不會壞掉既有功能。

---

## 六、流程總結

```
需求確認
   ↓
模組設計（拆分功能、定義介面）
   ↓
撰寫程式碼
   ↓
撰寫測試（單元測試）
   ↓
Code Review
   ↓
CI 自動測試通過
   ↓
合併主線
```

這個流程不是銀彈，但它能讓團隊在專案規模變大時，仍然維持一定的品質底線。

---

## 小結

| | C | Python |
|---|---|---|
| 建置工具 | Makefile / CMake | pyproject.toml / pip |
| 測試框架 | Unity / assert | pytest |
| 靜態分析 | gcc -Wall | mypy / ruff |
| CI 整合 | GitHub Actions | GitHub Actions |

好的軟體品質不是靠一次性的努力，而是靠流程的持續累積。
從小專案就開始養成測試與 CI 的習慣，未來接手大專案時會輕鬆很多。
