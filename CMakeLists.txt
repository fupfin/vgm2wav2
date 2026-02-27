cmake_minimum_required(VERSION 3.12)
project(vgm2wav2)

# libvgm의 vgm2wav 빌드를 활성화
set(BUILD_VGM2WAV ON CACHE BOOL "build vgm2wav application" FORCE)

add_subdirectory(libvgm)

# --- vgm2wav2: single/folder/zip 일괄 변환 ---
find_package(libzip REQUIRED
    HINTS /opt/homebrew /opt/homebrew/lib/cmake/libzip)

add_executable(vgm2wav2 vgm2wav2.cpp)
set_target_properties(vgm2wav2 PROPERTIES CXX_STANDARD 17)
target_include_directories(vgm2wav2 PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/libvgm)
target_link_libraries(vgm2wav2 PRIVATE vgm-player vgm-emu vgm-utils libzip::zip)
