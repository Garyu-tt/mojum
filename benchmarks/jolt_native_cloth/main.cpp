// Mojum native Jolt soft-body cloth probe.
// Upstream API pattern follows JoltPhysics v5.7.0 HelloWorld and
// UnitTests/Physics/SoftBodyTests.cpp. This file is intentionally a tiny,
// headless synthetic smoke test, not a production garment solver.

#include <Jolt/Jolt.h>
#include <Jolt/RegisterTypes.h>
#include <Jolt/Core/Factory.h>
#include <Jolt/Core/TempAllocator.h>
#include <Jolt/Core/JobSystemThreadPool.h>
#include <Jolt/Physics/PhysicsSystem.h>
#include <Jolt/Physics/SoftBody/SoftBodySharedSettings.h>
#include <Jolt/Physics/SoftBody/SoftBodyCreationSettings.h>
#include <Jolt/Physics/SoftBody/SoftBodyMotionProperties.h>

#include <algorithm>
#include <cstdarg>
#include <cstdio>
#include <cmath>
#include <iostream>
#include <thread>

using namespace JPH;

namespace {
constexpr ObjectLayer kStaticLayer = 0;
constexpr ObjectLayer kMovingLayer = 1;

namespace BroadLayers {
constexpr BroadPhaseLayer kStatic(0);
constexpr BroadPhaseLayer kMoving(1);
constexpr uint kCount = 2;
}

class BroadPhaseMap final : public BroadPhaseLayerInterface {
public:
    BroadPhaseMap() {
        object_to_broad_[kStaticLayer] = BroadLayers::kStatic;
        object_to_broad_[kMovingLayer] = BroadLayers::kMoving;
    }
    uint GetNumBroadPhaseLayers() const override { return BroadLayers::kCount; }
    BroadPhaseLayer GetBroadPhaseLayer(ObjectLayer layer) const override {
        JPH_ASSERT(layer < 2);
        return object_to_broad_[layer];
    }
private:
    BroadPhaseLayer object_to_broad_[2];
};

class ObjectVsBroadFilter final : public ObjectVsBroadPhaseLayerFilter {
public:
    bool ShouldCollide(ObjectLayer object, BroadPhaseLayer broad) const override {
        if (object == kStaticLayer) return broad == BroadLayers::kMoving;
        if (object == kMovingLayer) return true;
        return false;
    }
};

class ObjectPairFilter final : public ObjectLayerPairFilter {
public:
    bool ShouldCollide(ObjectLayer a, ObjectLayer b) const override {
        if (a == kStaticLayer) return b == kMovingLayer;
        if (a == kMovingLayer) return true;
        return false;
    }
};

void TraceToStderr(const char *format, ...) {
    va_list args;
    va_start(args, format);
    std::vfprintf(stderr, format, args);
    std::fputc('\n', stderr);
    va_end(args);
}

Ref<SoftBodySharedSettings> MakeCloth(uint count_x, uint count_z, float spacing) {
    auto settings = Ref<SoftBodySharedSettings>(new SoftBodySharedSettings);
    for (uint z = 0; z < count_z; ++z) {
        for (uint x = 0; x < count_x; ++x) {
            SoftBodySharedSettings::Vertex vertex;
            vertex.mPosition = Float3(
                (static_cast<float>(x) - 0.5f * static_cast<float>(count_x - 1)) * spacing,
                0.0f,
                (static_cast<float>(z) - 0.5f * static_cast<float>(count_z - 1)) * spacing);
            // Pin the complete first row. Other vertices are dynamic particles.
            vertex.mInvMass = (z == 0) ? 0.0f : 1.0f;
            settings->mVertices.push_back(vertex);
        }
    }

    const auto index = [count_x](uint x, uint z) { return x + z * count_x; };
    for (uint z = 0; z + 1 < count_z; ++z) {
        for (uint x = 0; x + 1 < count_x; ++x) {
            settings->AddFace(SoftBodySharedSettings::Face(
                index(x, z), index(x, z + 1), index(x + 1, z)));
            settings->AddFace(SoftBodySharedSettings::Face(
                index(x + 1, z), index(x, z + 1), index(x + 1, z + 1)));
        }
    }

    // Finite compliance values plus LRA keep this deliberately small cloth
    // stable under gravity while leaving it visibly deformable.
    const SoftBodySharedSettings::VertexAttributes attributes(
        1.0e-4f, 1.0e-4f, 1.0e-3f,
        SoftBodySharedSettings::ELRAType::EuclideanDistance, 1.1f);
    settings->CreateConstraints(&attributes, 1,
        SoftBodySharedSettings::EBendType::Distance);
    settings->Optimize();
    return settings;
}

bool IsFinite(const Vec3 &v) {
    return std::isfinite(v.GetX()) && std::isfinite(v.GetY()) && std::isfinite(v.GetZ());
}
} // namespace

int main() {
    RegisterDefaultAllocator();
    Trace = TraceToStderr;
    Factory::sInstance = new Factory();
    RegisterTypes();

    constexpr uint max_bodies = 16;
    constexpr uint max_pairs = 64;
    constexpr uint max_contacts = 64;
    BroadPhaseMap broad_phase;
    ObjectVsBroadFilter object_vs_broad;
    ObjectPairFilter object_pairs;

    PhysicsSystem physics;
    physics.Init(max_bodies, 0, max_pairs, max_contacts,
        broad_phase, object_vs_broad, object_pairs);

    TempAllocatorImpl temp_allocator(2 * 1024 * 1024);
    const uint hardware_threads = std::max(1u, std::thread::hardware_concurrency());
    // This probe simulates only 96 vertices; cap background workers so a
    // large host does not spend more resources on scheduling than the cloth.
    constexpr uint max_worker_threads = 8;
    const uint worker_threads = std::min(
        max_worker_threads, std::max(1u, hardware_threads - 1u));
    JobSystemThreadPool jobs(cMaxPhysicsJobs, cMaxPhysicsBarriers, worker_threads);

    constexpr uint count_x = 12;
    constexpr uint count_z = 8;
    constexpr uint steps = 240;
    constexpr float dt = 1.0f / 60.0f;
    constexpr float spawn_y = 3.0f;
    auto shared = MakeCloth(count_x, count_z, 0.12f);

    SoftBodyCreationSettings body_settings(
        shared, RVec3(0, spawn_y, 0),
        Quat::sIdentity(), kMovingLayer);
    body_settings.mAllowSleeping = false;
    body_settings.mNumIterations = 10;

    BodyInterface &body_interface = physics.GetBodyInterface();
    Body *body = body_interface.CreateSoftBody(body_settings);
    if (body == nullptr) {
        std::cerr << "failed to create soft body\n";
        UnregisterTypes();
        delete Factory::sInstance;
        Factory::sInstance = nullptr;
        return 2;
    }
    const BodyID body_id = body->GetID();
    body_interface.AddBody(body_id, EActivation::Activate);

    physics.OptimizeBroadPhase();
    bool finite_state = true;
    for (uint i = 0; i < steps; ++i) {
        physics.Update(dt, 1, &temp_allocator, &jobs);
        const auto *motion = static_cast<const SoftBodyMotionProperties *>(body->GetMotionProperties());
        for (uint v = 0; v < shared->mVertices.size(); ++v)
            finite_state = finite_state && IsFinite(motion->GetVertex(v).mPosition);
    }

    const auto *motion = static_cast<const SoftBodyMotionProperties *>(body->GetMotionProperties());
    float bottom_y_sum = 0.0f;
    float max_pinned_drift = 0.0f;
    for (uint x = 0; x < count_x; ++x) {
        const uint bottom = x + (count_z - 1) * count_x;
        const RVec3 world = body->GetCenterOfMassTransform() * motion->GetVertex(bottom).mPosition;
        bottom_y_sum += static_cast<float>(world.GetY());
        const uint pinned = x;
        const RVec3 pinned_world = body->GetCenterOfMassTransform() * motion->GetVertex(pinned).mPosition;
        const float dx = static_cast<float>(pinned_world.GetX()) -
            static_cast<float>(shared->mVertices[pinned].mPosition.x);
        const float dy = static_cast<float>(pinned_world.GetY()) -
            (spawn_y + static_cast<float>(shared->mVertices[pinned].mPosition.y));
        const float dz = static_cast<float>(pinned_world.GetZ()) -
            static_cast<float>(shared->mVertices[pinned].mPosition.z);
        max_pinned_drift = std::max(max_pinned_drift, std::sqrt(dx * dx + dy * dy + dz * dz));
    }

    const float mean_bottom_drop = spawn_y -
        (bottom_y_sum / static_cast<float>(count_x));
    const bool deformed = finite_state && mean_bottom_drop > 0.01f;
    const bool pinned_ok = max_pinned_drift < 1.0e-3f;

    std::cout.precision(9);
    std::cout << "{\"schema\":\"mojum.jolt-native-cloth/1\","
        << "\"engine_version\":\"5.7.0\","
        << "\"steps\":" << steps << ","
        << "\"fixed_dt_seconds\":" << dt << ","
        << "\"vertices\":" << shared->mVertices.size() << ","
        << "\"faces\":" << shared->mFaces.size() << ","
        << "\"pinned_vertices\":" << count_x << ","
        << "\"mean_bottom_drop_m\":" << mean_bottom_drop << ","
        << "\"max_pinned_drift_m\":" << max_pinned_drift << ","
        << "\"finite_state\":" << (finite_state ? "true" : "false") << ","
        << "\"pinned_row_stable\":" << (pinned_ok ? "true" : "false") << ","
        << "\"deformed\":" << (deformed ? "true" : "false") << ","
        << "\"self_collision\":false,"
        << "\"external_contact\":false"
        << "}\n";

    body_interface.RemoveBody(body_id);
    body_interface.DestroyBody(body_id);
    UnregisterTypes();
    delete Factory::sInstance;
    Factory::sInstance = nullptr;

    return (finite_state && pinned_ok && deformed) ? 0 : 3;
}
