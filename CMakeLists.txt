cmake_minimum_required(VERSION 3.12)
project(vgm2wav2)

# libvgm의 vgm2wav 빌드를 활성화
set(BUILD_VGM2WAV ON CACHE BOOL "build vgm2wav application" FORCE)

add_subdirectory(libvgm)
