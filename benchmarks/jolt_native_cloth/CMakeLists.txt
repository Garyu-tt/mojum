cmake_minimum_required(VERSION 3.20)
project(MojumJoltNativeClothProbe LANGUAGES CXX)

include(FetchContent)

# This is an isolated native probe: Jolt is fetched only for this build and
# never becomes a mandatory dependency of the Python package.
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(JPH_BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(ENABLE_ALL_WARNINGS OFF CACHE BOOL "" FORCE)
set(ENABLE_INSTALL OFF CACHE BOOL "" FORCE)
set(DEBUG_RENDERER_IN_DEBUG_AND_RELEASE OFF CACHE BOOL "" FORCE)
set(PROFILER_IN_DEBUG_AND_RELEASE OFF CACHE BOOL "" FORCE)
set(INTERPROCEDURAL_OPTIMIZATION OFF CACHE BOOL "" FORCE)

FetchContent_Declare(
  JoltPhysics
  GIT_REPOSITORY https://github.com/jrouwe/JoltPhysics.git
  GIT_TAG v5.7.0
  GIT_SHALLOW TRUE
  SOURCE_SUBDIR Build
)
FetchContent_MakeAvailable(JoltPhysics)

add_executable(jolt_native_cloth_probe main.cpp)
target_link_libraries(jolt_native_cloth_probe PRIVATE Jolt)
target_compile_features(jolt_native_cloth_probe PRIVATE cxx_std_17)
set_target_properties(jolt_native_cloth_probe PROPERTIES
  CXX_EXTENSIONS OFF
  INTERPROCEDURAL_OPTIMIZATION OFF
)
