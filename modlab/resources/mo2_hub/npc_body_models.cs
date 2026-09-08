public static void ApplyModLabModels(Npc npc, IArmorGetter skin, string actor, PatcherSettings settings, ILinkCache<ISkyrimMod, ISkyrimModGetter> cache, ISkyrimMod patch)
        {
            if (!settings.ModLabBodyModelSources.TryGetValue(actor, out var models))
            {
                npc.WornArmor.SetTo(skin.FormKey);
                return;
            }
            if (!settings.ModLabBodySex.TryGetValue(actor, out var sex) || (sex != "female" && sex != "male"))
                throw new Exception("Character body sex is missing or unsupported");
            var previousNext = patch.NextFormID;
            patch.NextFormID = ModLabStableId(patch, actor + "|body");
            var copy = patch.Armors.DuplicateInAsNewRecord(skin);
            patch.NextFormID = Math.Max(previousNext, patch.NextFormID);
            copy.EditorID = "ModLabBody_" + npc.FormKey.ID.ToString("X6");
            copy.Armature.Clear();
            foreach (var link in skin.Armature)
            {
                if (!models.TryGetValue(link.FormKey.ToString().ToLowerInvariant(), out var modelKey))
                {
                    copy.Armature.Add(link);
                    continue;
                }
                var original = cache.Resolve<IArmorAddonGetter>(link.FormKey);
                var donor = cache.Resolve<IArmorAddonGetter>(FormKey.Factory(modelKey));
                previousNext = patch.NextFormID;
                patch.NextFormID = ModLabStableId(patch, actor + "|part|" + link.FormKey.ToString());
                var addon = patch.ArmorAddons.DuplicateInAsNewRecord(original);
                patch.NextFormID = Math.Max(previousNext, patch.NextFormID);
                addon.WorldModel ??= new GenderedItem<Model>(null, null);
                addon.FirstPersonModel ??= new GenderedItem<Model>(null, null);
                if (addon.WorldModel == null) throw new Exception("Shared body has no world model");
                if (sex == "female")
                {
                    addon.WorldModel.Female = donor.WorldModel?.Female?.DeepCopy();
                    if (addon.FirstPersonModel != null) addon.FirstPersonModel.Female = donor.FirstPersonModel?.Female?.DeepCopy();
                }
                else
                {
                    addon.WorldModel.Male = donor.WorldModel?.Male?.DeepCopy();
                    if (addon.FirstPersonModel != null) addon.FirstPersonModel.Male = donor.FirstPersonModel?.Male?.DeepCopy();
                }
                copy.Armature.Add(addon.AsLinkGetter());
            }
            npc.WornArmor.SetTo(copy.FormKey);
        }

        public static uint ModLabStableId(ISkyrimMod patch, string identity)
        {
            using var hash = System.Security.Cryptography.SHA256.Create();
            var bytes = hash.ComputeHash(System.Text.Encoding.UTF8.GetBytes(identity.ToLowerInvariant()));
            uint value = 0x800 + (((uint)bytes[0] << 16 | (uint)bytes[1] << 8 | bytes[2]) % 0xFFF800);
            if (patch.EnumerateMajorRecords().Any(r => r.FormKey.ModKey == patch.ModKey && r.FormKey.ID == value))
                throw new Exception("Generated character record ID collision. Output was withheld; existing choices remain installed.");
            return value;
        }

        