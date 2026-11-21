cmake_minimum_required(VERSION 3.20.0)

set(USER_CACHE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/.cache/zephyr" CACHE PATH "Zephyr cache")

if(NOT DTC_OVERLAY_FILE)
  set(DTC_OVERLAY_FILE "${CMAKE_CURRENT_SOURCE_DIR}/boards/nucleo_f401re.overlay")
endif()

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(lab5_consensus)
include(cmake/preemptive_can.cmake)

# Generate cluster_config.h from the one configuration file every node shares.
set(LAB_CLUSTER_CONF "${CMAKE_CURRENT_SOURCE_DIR}/config/cluster.conf")
set(LAB_CONFIG_HEADER "${CMAKE_CURRENT_BINARY_DIR}/lab5_generated/cluster_config.h")
add_custom_command(
  OUTPUT ${LAB_CONFIG_HEADER}
  COMMAND ${CMAKE_COMMAND} -E env ${PYTHON_EXECUTABLE}
          "${CMAKE_CURRENT_SOURCE_DIR}/tools/gen_config.py"
          "${LAB_CLUSTER_CONF}" "${LAB_CONFIG_HEADER}"
  DEPENDS ${LAB_CLUSTER_CONF}
          "${CMAKE_CURRENT_SOURCE_DIR}/tools/gen_config.py"
          "${CMAKE_CURRENT_SOURCE_DIR}/pi/demo/cluster_config.py"
  COMMENT "Generating cluster_config.h from config/cluster.conf"
  VERBATIM)
add_custom_target(lab5_config DEPENDS ${LAB_CONFIG_HEADER})
add_dependencies(app lab5_config)

set(LAB_NODE_ID "2" CACHE STRING "Nucleo node number: 2 (front) or 3 (rear)")
if(NOT LAB_NODE_ID MATCHES "^[23]$")
  message(FATAL_ERROR "LAB_NODE_ID must be 2 or 3")
endif()
set(LAB_TRACE_TASK "-1" CACHE STRING "GPIO PC11 envelope: task index 0..8, or -1 disabled")
set(LAB_MEASURE_MODE "0" CACHE STRING "0 normal, 1 RX only, 2 N3 DRIVE, 3 N2 RELAY")
if(NOT LAB_MEASURE_MODE MATCHES "^[0-3]$")
  message(FATAL_ERROR "LAB_MEASURE_MODE must be 0..3")
endif()
if((LAB_MEASURE_MODE STREQUAL "2" AND NOT LAB_NODE_ID STREQUAL "3") OR
   (LAB_MEASURE_MODE STREQUAL "3" AND NOT LAB_NODE_ID STREQUAL "2"))
  message(FATAL_ERROR "DRIVE measurement needs N3; RELAY measurement needs N2")
endif()
target_compile_definitions(app PRIVATE LAB_MEASURE_MODE=${LAB_MEASURE_MODE})
option(LAB_DIAGNOSTICS "Enable intrusive periodic console timing summaries" OFF)
option(LAB_PIN_SELFTEST "Walk the Lab 5 outputs once at boot so each can be identified" ON)
target_compile_definitions(app PRIVATE LAB_PIN_SELFTEST=$<BOOL:${LAB_PIN_SELFTEST}>)
target_compile_definitions(app PRIVATE LAB_DIAGNOSTICS=$<BOOL:${LAB_DIAGNOSTICS}> LAB_NODE_ID=${LAB_NODE_ID} LAB_TRACE_TASK=${LAB_TRACE_TASK})

target_sources(app PRIVATE
  src/main.c
  src/lab_protocol.c
  src/lab_io.c
  src/lab_can.c
  src/lab_tasks.c
  src/raft_agent.c
  src/raft_log.c
  src/network_state.c
  src/time_sync.c
)
target_include_directories(app PRIVATE include "${CMAKE_CURRENT_BINARY_DIR}/lab5_generated")
