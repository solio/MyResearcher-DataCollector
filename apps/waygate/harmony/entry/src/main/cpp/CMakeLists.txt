cmake_minimum_required(VERSION 3.5.0)
project(waygate_ssh)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

add_library(waygate_ssh SHARED
    napi_init.cpp
)

target_link_libraries(waygate_ssh PUBLIC
    libace_napi.z.so
)
