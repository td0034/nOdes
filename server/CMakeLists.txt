cmake_minimum_required(VERSION 3.10)

# Project name and version
project(multicast_sender VERSION 1.0)

# Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED True)
find_package(Eigen3 3.3 REQUIRED NO_MODULE)


# Add the executable
add_executable(
    multicast_sender 
    src/multicast_sender.cpp
)

# Link pthread for POSIX scheduling and thread affinity
target_link_libraries(multicast_sender pthread fmt ncurses Eigen3::Eigen)