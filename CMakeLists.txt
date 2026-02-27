cmake_minimum_required(VERSION 3.12)
project(vgm2wav2)

# libvgm의 vgm2wav 빌드를 활성화
set(BUILD_VGM2WAV ON CACHE BOOL "build vgm2wav application" FORCE)

add_subdirectory(libvgm)

# --- vgm2wav2: single/folder/zip 일괄 변환 ---
find_package(libzip QUIET
    HINTS /opt/homebrew /opt/homebrew/lib/cmake/libzip)

add_executable(vgm2wav2 vgm2wav2.cpp)
set_target_properties(vgm2wav2 PROPERTIES CXX_STANDARD 17)
target_include_directories(vgm2wav2 PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/libvgm)
target_link_libraries(vgm2wav2 PRIVATE vgm-player vgm-emu vgm-utils)

if(libzip_FOUND)
    message(STATUS "libzip found: ZIP support enabled")
    target_compile_definitions(vgm2wav2 PRIVATE HAVE_LIBZIP)
    target_link_libraries(vgm2wav2 PRIVATE libzip::zip)
else()
    message(STATUS "libzip not found: ZIP support disabled")
endif()

find_package(PkgConfig)
if(PkgConfig_FOUND)
    pkg_check_modules(LIBGME QUIET libgme)
endif()

if(LIBGME_FOUND)
    message(STATUS "libgme found: GME engine support enabled")
    target_compile_definitions(vgm2wav2 PRIVATE HAVE_GME)
    target_include_directories(vgm2wav2 PRIVATE ${LIBGME_INCLUDE_DIRS})
    target_link_libraries(vgm2wav2 PRIVATE ${LIBGME_LIBRARIES})
    target_link_directories(vgm2wav2 PRIVATE ${LIBGME_LIBRARY_DIRS})
else()
    message(STATUS "libgme not found: GME engine disabled (brew install game-music-emu)")
endif()
